"""Delegate ``python -m archflow.runtime`` to the formal project runtime."""

from archflow.project.runtime import main


raise SystemExit(main())
