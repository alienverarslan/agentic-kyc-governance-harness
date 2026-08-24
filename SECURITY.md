# Security Policy

## Scope

This repository is a research and portfolio project. It runs on **synthetic data only** and
is not a production system, so "vulnerability" here mostly means one of:

* a way to make the harness report a safer-looking result than the run actually produced —
  for example a path where an operational failure is swallowed instead of escalating, or
  where a contaminated attempt could be scored as valid;
* a way to make a published evidence artifact disagree with the immutable session artifact
  it derives from;
* an accidental disclosure in a tracked file, such as a credential, a real dossier, or
  model output that was never approved for release.

Ordinary bugs and questions belong in a normal issue.

## Reporting

Please report suspected security issues **privately**.

The intended channel is GitHub's **private vulnerability reporting**: the repository's
**Security** tab, then **Report a vulnerability**, which opens a private advisory visible
only to the maintainer. That feature is a repository setting and is **not yet enabled**;
enabling and verifying it is a release step for this project, tracked below.

Until it is enabled and this note is removed, do not open a public issue describing a
suspected disclosure. Contact the maintainer through their GitHub profile and ask for a
private channel first.

In any report: do not include real credentials or real customer data. If you believe a
credential has been exposed, say where it appears and stop there — redact the value itself.

There is no bug bounty and no service-level commitment. This is a single-maintainer
project; expect a best-effort response.

**Release step, not yet done:** enable private vulnerability reporting in repository
settings once the repository is public, confirm the **Report a vulnerability** button is
visible on the Security tab, then delete the two paragraphs above that describe the
interim arrangement.

## Supported versions

Only the current `main` branch is maintained. The invariant that matters for provenance is
narrower than "branches are kept": **commits that published evidence remain reachable from
`main` history and are never rewritten**, because the write-ups cite them by SHA. Merged
remote branches may be deleted at any time — deleting a merged branch does not remove its
commits from `main`. Old commits are never patched in place.

## Credentials

No credential is committed to this repository, and none is required to run the test suite.
The live provider path is operator-initiated and reads `ANTHROPIC_API_KEY` from the
environment; `.env` is gitignored and `.env.example` ships with an empty value.

`tests/test_system_errors.py` contains a deliberately fake, non-functional API-key-shaped
string. It is a test fixture: the test asserts that a provider error carrying such a value
never reaches an auditable record. It is not a credential and does not need rotation.
