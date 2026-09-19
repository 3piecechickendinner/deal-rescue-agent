# prep/

Personal preparation material. **Not part of the deliverable.**

- `narration.txt` — spoken-word walkthrough of the system.

Generate audio from it:

```bash
say -v Samantha -r 180 -o prep/walkthrough.m4a --data-format=aac -f prep/narration.txt
```

`-r` is words per minute. The generated audio is gitignored — it is ~2.4 MB and
reproducible from the script above, so there is no reason to version it.

Remove this directory before sharing the repository:

```bash
git rm -r --cached prep && echo "prep/" >> .gitignore
```
