# AI usage

This project was built with substantial help from AI, and you should weigh that
when deciding whether to rely on it.

- **Design and early tuning:** the approach, the tag family choice and the
  first detector settings were worked out in conversation with an AI assistant.
  The maintainer ran test scripts on real camera frames and fed the results
  back. Several of the AI's assumptions were later shown to be wrong by
  measurement: for example, contrast enhancement (CLAHE) was expected to help
  and in fact hurt. The settings that shipped are the ones that held up in
  testing.
- **Code, tests and documentation:** the source code, test suite and
  documentation, including these pages, were written by an AI coding agent
  (Anthropic's Claude, through Claude Code). The maintainer directed the work.
- **What the maintainer did:** set requirements and constraints, made or
  approved the design decisions and trade-offs, reviewed the plans, supplied
  the real camera frames used for calibration, and installed and tested each
  release on their own Home Assistant system.
- **How the numbers were checked:** thresholds such as the smear threshold and
  the shape gate were set from measurements on real frames (day and IR night,
  from one camera), not from the AI's reasoning alone. The test suite runs
  against both synthetic and real frames.
- **What has not been done:** there has been no independent human code review
  or security review. Testing covers one camera for each part.

Treat it as experimental software. Read the code before you rely on it for
anything that matters, and especially before letting it near a lock.
