"""Delegate ``python -m archflow.runtime`` to the formal project runtime."""

from archive.archflow.project.runtime import main


raise SystemExit(main())
