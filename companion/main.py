"""Frozen executable entry; multiprocessing must divert workers before the GUI."""
import multiprocessing
if __name__=='__main__':
    multiprocessing.freeze_support()
    from contentrium_cut.launcher import main
    raise SystemExit(main())
