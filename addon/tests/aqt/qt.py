class _Sig:
    def __init__(self): self.fns = []
class QAction:
    def __init__(self, *a): self.triggered = _Sig()
class QMenu:
    def __init__(self, *a): pass
    def addAction(self, a): pass
class QDialog:
    def __init__(self, *a): pass
    def setWindowTitle(self, t): self.title = t
    def resize(self, *a): pass
    def show(self): self.shown = True
class QVBoxLayout:
    def __init__(self, *a): pass
    def setContentsMargins(self, *a): pass
    def addWidget(self, w): pass
class QUrl:
    def __init__(self, u): self.u = u
def qconnect(sig, fn): sig.fns.append(fn)
