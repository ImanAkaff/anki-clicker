from aqt.qt import _Sig
class AnkiWebView:
    last = None
    def __init__(self, parent=None): self.loadFinished = _Sig(); self.evals = []; AnkiWebView.last = self
    def set_open_links_externally(self, v): self.external = v
    def load_url(self, url): self.url = url.u; assert self.external is False, "would open externally"
    def eval(self, js): self.evals.append(js)
