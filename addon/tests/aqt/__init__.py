import sqlite3, types
gui_hooks = types.SimpleNamespace(**{n: [] for n in [
    "webview_will_set_content","webview_did_receive_js_message","reviewer_did_show_question",
    "reviewer_did_show_answer","reviewer_did_answer_card","reviewer_will_end","profile_did_open"]})
class _DB:
    def __init__(self):
        self.c = sqlite3.connect(":memory:"); self.c.execute("create table revlog (id integer primary key, cid integer, ease integer)")
        self.c.execute("create table cards (id integer primary key, did integer, odid integer)")
    def scalar(self, sql, *a): return self.c.execute(sql, a).fetchone()[0]
    def list(self, sql, *a): return [r[0] for r in self.c.execute(sql, a)]
    def all(self, sql, *a): return self.c.execute(sql, a).fetchall()
class _AM:
    cfg = {"server_url": "https://anki-clicker-api.onrender.com", "token": "", "sync_enabled": True, "button_map": {}}
    written = None
    def addonFromModule(self, m): return "anki_clicker"
    def setWebExports(self, *a): pass
    def getConfig(self, m): return dict(self.cfg)
    def writeConfig(self, m, c): _AM.written = c; _AM.cfg = c
class _Card:
    def __init__(self, cid): self.cid = cid
    def question(self): return f"<style>.x{{}}</style><div>What is card {self.cid}?</div><br>[sound:a.mp3]"
class _Col:
    db = _DB()
    # Deck 1 = MCAT::Bio, deck 2 = MCAT::Chem, deck 99 = a filtered (custom study) deck
    _names = {1: "MCAT::Bio", 2: "MCAT::Chem", 99: "Filtered Deck 1"}
    decks = types.SimpleNamespace(
        name_if_exists=lambda d: _Col._names.get(d),
        get=lambda d, default=False: ({"id": d, "dyn": d == 99} if d in _Col._names else None))
    def get_card(self, cid): return _Card(cid)
class _TM:
    def run_in_background(self, task, done): done(types.SimpleNamespace(result=lambda: types.SimpleNamespace(error="(fake) offline", uploaded=0)))
mw = types.SimpleNamespace(col=_Col(), addonManager=_AM(), form=types.SimpleNamespace(menuTools=types.SimpleNamespace(addMenu=lambda m: None)),
     state="review", reviewer=None, taskman=_TM(), serverURL=lambda: "http://127.0.0.1:40000/")
