"""One public executable; no dependency on the replaceable editor runtime."""
from dolly.release_launcher import main
from dolly.update_worker import notify

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        notify("Dolly could not start: " + str(error))
        raise SystemExit(1)
