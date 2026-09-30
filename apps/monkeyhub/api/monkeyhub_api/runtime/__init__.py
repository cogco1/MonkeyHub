"""The Hub's side of the Project Runtimes it starts, one per open project.

``applications`` owns the application cards and builds each child's launch,
``workers`` starts and supervises those processes, ``manager`` attaches the
projects, admits and forwards their requests and relays their events, and
``models`` holds the runtime snapshot and event DTOs.
"""
