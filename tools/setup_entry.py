"""Capture failures before heavy Setup imports; CLI diagnosis never creates UI."""
import argparse
import importlib
import json
from pathlib import Path
import sys
from contentrium_cut.installer_reporting import failure,save_diagnostic,write_report


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    early=argparse.ArgumentParser(add_help=False);early.add_argument('--result');early.add_argument('--tag')
    known,_=early.parse_known_args(argv)
    try:return importlib.import_module('tools.install_entry').main(argv)
    except Exception as error:
        version=known.tag[1:] if known.tag and known.tag.startswith('v') else None
        if version is None:
            try:version=json.loads((Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))/'config.json').read_text(encoding='utf-8'))['appVersion']
            except (OSError,ValueError,KeyError):pass
        report=failure(error,'startup-import',version=version)
        try:log=save_diagnostic(report)
        except (OSError,ValueError):log=None
        if known.result:
            try:write_report(known.result,report)
            except (OSError,ValueError):pass
        if sys.stderr:print(report['error']['code'],report['error']['exceptionType'],file=sys.stderr)
        if '--diagnose' not in argv and '--install' not in argv and '--help' not in argv:
            try:
                from contentrium_cut.installer_ui import InstallerWindow
                view=InstallerWindow('설치 시작 확인');view.failure(report,log);view.primary_button.configure(text='닫기',command=view.window.destroy);view.cancel_button.configure(command=view.window.destroy);view.window.mainloop()
            except Exception:pass  # Parent result remains available if Tcl itself failed.
        return 1


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
