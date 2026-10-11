shown = []; answers = []
def showInfo(msg, title=None): shown.append(msg)
def tooltip(msg, period=None): shown.append("TOOLTIP: " + msg)
def getText(prompt, parent=None, title=None): return answers.pop(0)
asked = []
def askUser(msg, parent=None, **k): asked.append(msg); return True
