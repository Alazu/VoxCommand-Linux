"""Turn transcribed speech into a command via fuzzy phrase matching.

Phrase syntax:
  "landing gear"                 plain phrase
  "[open|show] [the|] map"       alternatives; an empty alternative makes the word optional
  "type *"                       trailing * captures the rest of the utterance as {text}
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from .config import Command

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def _number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens - 2] + (" " + _ONES[ones] if ones else "")
    return " ".join(_ONES[int(d)] for d in str(n))


def normalize(text: str) -> str:
    """Lowercase, drop punctuation and spell out digits so "Gear 2!" == "gear two"."""
    text = text.lower().replace("'", "").replace("’", "")
    text = re.sub(r"\d+", lambda m: f" {_number_words(int(m.group()))} ", text)
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def expand(phrase: str) -> list[str]:
    """Expand "[a|b] c" into ["a c", "b c"]."""
    m = re.search(r"\[([^\[\]]*)\]", phrase)
    if not m:
        return [" ".join(phrase.split())]
    out = []
    for alt in re.split(r"[|;]", m.group(1)):
        for variant in expand(phrase[: m.start()] + alt + phrase[m.end() :]):
            if variant not in out:
                out.append(variant)
    return out


@dataclass
class Match:
    command: Command
    score: float
    phrase: str
    text: str = ""  # wildcard capture


class Matcher:
    def __init__(self, commands: list[Command], threshold: float = 82.0):
        self.threshold = threshold
        self.exact: list[tuple[str, Command]] = []
        self.wildcard: list[tuple[str, Command]] = []
        for cmd in commands:
            for phrase in cmd.phrases:
                for variant in expand(phrase):
                    if variant.endswith("*"):
                        self.wildcard.append((normalize(variant[:-1]), cmd))
                    else:
                        self.exact.append((normalize(variant), cmd))

    def vocabulary(self) -> list[str]:
        """All phrases, used to bias Whisper towards the profile's wording."""
        return [p for p, _ in self.exact] + [p for p, _ in self.wildcard]

    def match_prefix(self, raw: str) -> Match | None:
        """The utterance is only the start of a wildcard phrase (e.g. just "type"): the words follow next."""
        text = normalize(raw).replace(" ", "")
        if not text:
            return None
        best = None
        for phrase, cmd in self.wildcard:
            score = fuzz.ratio(text, phrase.replace(" ", ""))
            if score >= self.threshold and (best is None or (score, len(phrase)) > (best.score, len(best.phrase))):
                best = Match(cmd, score, phrase + " *")
        return best

    def match(self, raw: str) -> Match | None:
        text = normalize(raw)
        if not text:
            return None
        best: Match | None = None
        for phrase, cmd in self.exact:
            score = fuzz.ratio(text, phrase)
            if score >= self.threshold and (best is None or score > best.score):
                best = Match(cmd, score, phrase)
        # Wildcards only win when no plain phrase fits, so "type" commands can't shadow real ones.
        if best is None:
            words = raw.split()
            rank = None
            for phrase, cmd in self.wildcard:
                target = phrase.replace(" ", "")
                n = len(phrase.split())
                # Whisper may split or join words ("DuckDuckGo" -> "duck duck go", "startpage" -> "start page")
                # and normalisation can change the count ("F12" -> "f one two"), so try a range of splits and
                # compare without spaces.
                for k in range(max(1, n - 1), min(len(words) - 1, n + 4) + 1):
                    score = fuzz.ratio(normalize(" ".join(words[:k])).replace(" ", ""), target)
                    if score < self.threshold:
                        continue
                    # Ties: the more specific phrase wins ("search google *" beats "search *"), then the
                    # split that consumes more words (so an optional trailing "for" isn't left in the text).
                    candidate = (score, len(target), k)
                    if rank is None or candidate > rank:
                        rank = candidate
                        text = " ".join(words[k:]).strip().rstrip(".!?,;:")
                        best = Match(cmd, score, phrase + " *", text)
        return best


def strip_wake_word(raw: str, wake_word: str, threshold: float = 80.0) -> str | None:
    """Return the utterance without its leading wake word, or None if it wasn't said."""
    wake = normalize(wake_word)
    n = len(wake.split())
    words = raw.split()
    for k in (n, n + 1, n - 1):
        if 0 < k <= len(words) and fuzz.ratio(normalize(" ".join(words[:k])), wake) >= threshold:
            return " ".join(words[k:])
    return None
