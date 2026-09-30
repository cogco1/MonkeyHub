"""The Hub's side of the Project Runtimes it starts, one per open project.

``applications`` owns the application cards and builds each child's launch,
``workers`` starts and supervises those processes, ``manager`` attaches the
projects, observes them, forwards their requests and relays their events,
``operations`` admits each forwarded mutation and keeps its recovery journal,
``worker_http`` is the Hub's HTTP to a worker, one request or its event
stream, and ``models`` holds the runtime snapshot and event DTOs.
"""
