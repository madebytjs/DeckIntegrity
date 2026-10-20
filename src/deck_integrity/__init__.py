"""DeckIntegrity - non-destructive Linux/SteamOS data-integrity diagnostics."""

VERSION = '0.3.0'

# Shown by the app before the first test and in the command-line help. Keep in step
# with the Disclaimer section of README.md.
DISCLAIMER = (
    'DeckIntegrity is free, unofficial software provided as is, without warranty of any kind, '
    'and you use it entirely at your own risk. It is designed to be non-destructive, but it writes '
    'and reads large test files and keeps your device busy, which adds heat, battery drain and some '
    'SSD wear. The authors are not responsible for any damage, data loss, failed repairs, voided '
    'warranties or other problems with your device. Back up anything important first. Results are '
    'evidence to help an investigation, not a diagnosis: a pass does not prove a device is healthy '
    'and a failure does not prove which part is faulty. DeckIntegrity is not affiliated with or '
    'endorsed by Valve, Lenovo or any device maker; Steam, Steam Deck and SteamOS are trademarks '
    'of Valve Corporation, and Legion is a trademark of Lenovo.')
