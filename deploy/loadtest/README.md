Load testing. Always test on hardware like your real server, and turn the nginx rate limits off for the test
(they are per IP, so one load generator trips them immediately): remove the `limit_req` lines in nginx.conf,
rebuild the nginx image, and put them back afterwards.

    # a token to use
    curl -s -X POST http://SERVER/api/signup -H 'content-type: application/json' -d '{"name":"load1","password":"secret12"}'

    # bets (paste the token into wrk-play.lua first)
    wrk -t4 -c200 -d30s -s wrk-play.lua http://SERVER/api/play
    # signups (CPU heavy on purpose, passwords are hashed with scrypt)
    wrk -t2 -c50 -d20s -s wrk-signup.lua http://SERVER/api/signup
    # page and sponsors
    wrk -t4 -c200 -d20s http://SERVER/
    wrk -t4 -c200 -d20s http://SERVER/api/sponsors

Watch `docker stats`, `docker compose logs nginx` for 502/429, and Postgres with
`docker compose exec postgres psql -U tej -c "select state,count(*) from pg_stat_activity group by 1"`.
After a run, `python smoke_test.py http://SERVER` confirms the coin books still balance.

## Admission controller checks

From the project root, run `PYTHONPATH=server python -m unittest discover -s deploy/tests -v`. The async tests cover immediate low-traffic admission, a bounded burst queue, overload rejection, and gradual concurrency reduction/recovery. Run them in the same Python minor version used by the container.
