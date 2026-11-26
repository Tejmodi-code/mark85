import multiprocessing
import os

bind = "0.0.0.0:8000"
worker_class = "uvicorn_worker.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", multiprocessing.cpu_count()))
timeout = 30
graceful_timeout = 20
keepalive = 30
max_requests = 20000          # recycle workers now and then to cap memory growth
max_requests_jitter = 2000
worker_tmp_dir = "/dev/shm"
accesslog = None              # nginx already logs every request
