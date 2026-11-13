"""Find email addresses in Reddit posts, including obfuscated ones.

Run `python email_extract.py` to run the self-tests.
"""

import html
import re

FREE = {"gmail": "gmail.com", "outlook": "outlook.com", "hotmail": "hotmail.com", "yahoo": "yahoo.com"}
CTX = r"(?:e-?mail|mail|contact|reach|send|cv|resume|apply|write)"
PLAIN = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,}", re.I)
AT = r"\s*(?:[\[\(\{<]\s*(?:at|@|at-symbol)\s*[\]\)\}>]|/\s*at\s*/)\s*"
DOT = r"\s*(?:[\[\(\{<]\s*(?:dot|\.)\s*[\]\)\}>]|/\s*dot\s*/)\s*"
FREE_RE = re.compile(r"([a-z0-9._%+\-]+)@(" + "|".join(FREE) + r")\b(?!\.)", re.I)
IGNORED_DOMAINS = ("reddit.com", "redditmail.com", "example.com")


def extract(text: str) -> list[str]:
    t = html.unescape(text)
    t = t.replace("\\_", "_").replace("\\.", ".").replace("\\@", "@")  # reddit markdown escapes
    out = re.findall(r"mailto:([^)\s>\"']+)", t, re.I)                  # mailto links
    t2 = re.sub(AT, "@", t, flags=re.I)                                 # [at] (at) / AT /
    t2 = re.sub(DOT, ".", t2, flags=re.I)                               # [dot] (dot) / DOT /

    def bare(m):                                                        # ' at ' / ' dot ' near a context word
        s = re.sub(r"\s+dot\s+", ".", m.group(0), flags=re.I)
        # only the last ' at ' ("email me at jane at x dot com" -> "email me at jane@x.com")
        return re.sub(r"\s+at\s+(?!.*\s+at\s)", "@", s, count=1, flags=re.I)

    t2 = re.sub(CTX + r"[^\n]{0,40}", bare, t2, flags=re.I)
    t2 = re.sub(r"\s*@\s*", "@", t2)                                    # 'alex @ domain.email'
    out += PLAIN.findall(t2)
    for m in FREE_RE.finditer(t2):                                      # name@gmail -> name@gmail.com
        out.append(m.group(1) + "@" + FREE[m.group(2).lower()])
    clean = []
    for e in out:
        e = e.strip(".,;:)>]'\"").lower()
        if PLAIN.fullmatch(e) and e not in clean:
            clean.append(e)
    return clean


def find_email(text: str) -> str | None:
    for email in extract(text):
        if not email.endswith(IGNORED_DOMAINS):
            return email
    return None


if __name__ == "__main__":
    tests = [
        ("Email: omkarnagre777@gmail.com", ["omkarnagre777@gmail.com"]),
        ("[jobs@harrityllp.com](mailto:jobs@harrityllp.com)", ["jobs@harrityllp.com"]),
        ("Send CV to [daniel@football-winner.com]().", ["daniel@football-winner.com"]),
        ("Email: amy [at] erbosoft [dot] com", ["amy@erbosoft.com"]),
        ("Email: kpolevoy [at-symbol] gmail [dot] com", ["kpolevoy@gmail.com"]),
        ("Email: daurgam[at]gmail[dot]com", ["daurgam@gmail.com"]),
        ("Email: derick.realwebdev[at]gmail.com", ["derick.realwebdev@gmail.com"]),
        ("Email: alec / AT / potluri / DOT / dev", ["alec@potluri.dev"]),
        ("Contact: alex @ serendipia.email", ["alex@serendipia.email"]),
        ("email: info [at] higroup.si", ["info@higroup.si"]),
        ("Email: hoglan (dot) jd (at) gmail", ["hoglan.jd@gmail.com"]),
        ("email: carolchung.dev at gmail", ["carolchung.dev@gmail.com"]),
        ("Email: first\\_last@my\\.company.io", ["first_last@my.company.io"]),
        ("Email me at jane.doe at example dot com", ["jane.doe@example.com"]),
        ("Please send me your email directly so I can contact you", []),
        ("meet at 5pm, dot matrix printer, at home", []),
        ("Email: [email protected]", []),
        ("h t t p s [colan] // iamjenn [dot] carrd [dot] co", []),
    ]
    ok = 0
    for txt, want in tests:
        got = extract(txt)
        ok += got == want
        print("PASS" if got == want else "FAIL", repr(txt)[:62].ljust(64), got)
    print(f"\n{ok}/{len(tests)} passed")
    raise SystemExit(0 if ok == len(tests) else 1)
