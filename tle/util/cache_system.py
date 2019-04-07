from functools import lru_cache
from collections import namedtuple

import aiohttp
import logging
import json
import time

from tle.util import handle_conn as hc
from tle.util import codeforces_api as cf

class CacheSystem:
    # """
    #     Explanation: a pair of 'problems' returned from cf api may
    #     be the same (div 1 vs div 2). we pick one of them and call
    #     it 'base_problem' which will be used below:
    # """
    """
        ^ for now, we won't pick problems with the same name the user has solved
        there isn't a good way to do this with the current API
    """
    def __init__(self):
        self.contest_dict = None    # id => Contest
        self.contest_last_cache = None
        self.problem_dict = None    # name => problem
        self.problem_start = None   # id => start_time
        # self.problems = None
        # self.base_problems = None
        # this dict looks up a problem identifier and returns that of the base problem
        # self.problem_to_base = None

    async def get_contests(self, duration: int):
        now = time.time()
        if self.contest_last_cache is None or self.contest_dict is None or now - self.contest_last_cache > duration:
            await self.cache_contests()
        return self.contest_dict.values()

    async def force_update(self):
        await self.cache_contests()
        await self.cache_problems()

    async def try_disk(self):
        contests = hc.conn.fetch_contests()
        problem_res = hc.conn.fetch_problems()
        if not contests or not problem_res:
            await self.cache_contests()
            await self.cache_problems()
            return
        self.contest_dict = { c.id : c for c in contests }
        self.problem_dict = {
            problem.name : problem
            for problem, start_time in problem_res
        }
        self.problem_start = {
            problem.contest_identifier : start_time
            for problem, start_time in problem_res
        }

    async def cache_contests(self):
        try:
            contests = await cf.contest.list()
        except aiohttp.ClientConnectionError as e:
            print(e)
            return
        except cf.CodeforcesApiError as e:
            print(e)
            return
        self.contest_dict = {
            c.id : c
            for c in contests
        }
        self.contest_last_cache = time.time()
        rc = hc.conn.cache_contests(contests)
        logging.info(f'{rc} contests cached')

    async def cache_problems(self):
        if self.contest_dict is None:
            await self.cache_contests()
        try:
            problems, _ = await cf.problemset.problems()
        except aiohttp.ClientConnectionError as e:
            print(e)
            return
        except cf.CodeforcesApiError as e:
            print(e)
            return
        banned_tags = ['*special']
        self.problem_dict = {
            prob.name : prob    # this will discard some valid problems
            for prob in problems
            if prob.has_metadata() and not prob.tag_matches(banned_tags)
        }
        self.problem_start = {
            prob.contest_identifier : self.contest_dict[prob.contestId].startTimeSeconds
            for prob in self.problem_dict.values()
        }
        rc = hc.conn.cache_problems([
                (
                    prob.name, prob.contestId, prob.index,
                    self.contest_dict[prob.contestId].startTimeSeconds,
                    prob.rating, prob.type, json.dumps(prob.tags)
                )
                for prob in self.problem_dict.values()
            ])
        logging.info(f'{rc} problems cached')

    # this handle all the (rating, solved) pair and caching
    # the user only has to call this. don't call the other functions below
    async def get_rating_solved(self, handle: str, time_out: int = 3600):
        cached = self.user_rating_solved(handle)
        stamp, rating, solved = cached
        if stamp is None:  # try from disk first
            stamp, rating, solved = await self.retrieve_rating_solved(handle)
        if stamp is None or time.time() - stamp > time_out: # fetch from cf
            stamp, trating, tsolved = await self.fetch_rating_solved(handle)
            if trating is not None: rating = trating
            if tsolved is not None: solved = tsolved
            cached[:] = stamp, rating, solved
        return rating, solved

    @lru_cache(maxsize=15)
    def user_rating_solved(self, handle: str):
        # this works. it will actually return a reference
        # the cache is for repeated requests and maxsize limits RAM usage
        return [None, None, None]

    async def fetch_rating_solved(self, handle: str): # fetch from cf api
        try:
            info = await cf.user.info(handles=[handle])
            subs = await cf.user.status(handle=handle)
            info = info[0]
            solved = [sub.problem for sub in subs if sub.verdict == 'OK']
            solved = { prob.name for prob in solved if prob.has_metadata() }
            stamp = time.time()
            hc.conn.cache_cfuser_full(info + (json.dumps(list(solved)), stamp))
            return stamp, info.rating, solved
        except aiohttp.ClientConnectionError as e:
            logging.error(e)
        except cf.CodeforcesApiError as e:
            logging.error(e)
        return [None, None, None]

    async def retrieve_rating_solved(self, handle: str): # retrieve from disk
        res = hc.conn.fetch_rating_solved(handle)
        if res and all(r is not None for r in res):
            return res[0], res[1], set(json.loads(res[2]))
        return [None, None, None]

