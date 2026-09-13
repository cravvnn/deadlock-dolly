from dolly.update_worker import main, notify
if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        notify("Dolly updater could not start: " + str(error))
        raise SystemExit(1)
