<!-- Blind localization probe (ahd, no paper counterpart): can the WHERE be recovered from the
WHY sentence alone? A fresh model sees the identifier-stripped WHY and the component manifest
and guesses the component. Recorded per cluster as a covariate. Output JSON only. -->
An agent harness has the following components:
{components}

A diagnosis of one failure says (identifiers removed):
"{mechanism}"

Which component does this diagnosis most likely refer to? Answer with a single JSON object:
{"top3": ["<component id>", "<component id>", "<component id>"]} ordered from most to least
likely, using only ids from the list.
