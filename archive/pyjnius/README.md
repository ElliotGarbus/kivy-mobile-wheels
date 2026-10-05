# Archived: pyjnius Android wheels

pyjnius now publishes Android wheels on PyPI, so this repository no longer
builds it or lists it in the PEP 503 index.

`pyjnius.sh` is the recipe that produced the wheels previously hosted here
(fork `ElliotGarbus/pyjnius` @ `0342053c74785c2833586aa8e97527a209bda48c`,
branch `spike/android-universal-wheel`). It is not wired into CI. The paths
inside it assume it still lives at `recipes/android/pyjnius.sh`.

Existing GitHub Releases are left in place. Old lock files pin those asset
URLs directly and do not need the index. `generate_index.py` skips the
`pyjnius` project so a republish drops it from
`https://elliotgarbus.github.io/kivy-mobile-wheels/simple/`.
