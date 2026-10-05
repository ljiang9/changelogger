"""Enables `python -m changelogger`."""
if __package__:
    from .changelogger import main
else:
    from changelogger import main

if __name__ == "__main__":
    raise SystemExit(main())
