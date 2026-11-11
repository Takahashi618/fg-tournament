# FG Tournament Manager

[日本語](README.md)

A Windows tool for running fighting game tournaments with only a screen reader (PC-Talker or NVDA) and the keyboard. When you enter a result, it reads out the next matches and the standings. It can also update a viewer page in the browser, so spectators can follow the tournament.

This is a test version.

The manual is in [Manual (English).txt](<Manual (English).txt>). The Japanese manual is [Manual (Japanese).txt](<Manual (Japanese).txt>).

## Features

- Single elimination, double elimination and round robin league (2 to 64 players; double elimination needs 3 to 64)
- Separate match lengths for regular matches, the Final, the Losers Final, the Grand Final and the Third Place Match
- Grand Final Reset in double elimination, Third Place Match in single elimination
- Four views: matches by round, player progress, next matches, and standings
- Editing and undoing results. When an edit affects later matches, you confirm which results will be cleared first
- Saving and opening tournaments. The tournament is saved automatically after every result
- Automatic updates to a viewer page (optional; you need your own server)
- English and Japanese for the screen and for speech. Switch on the Language menu. The viewer page follows the language of the app (when the viewer.html on your server is from this version)

## Made with AI

The source code and documents were written with AI (Claude by Anthropic). The author decided what to build and whether it works well with speech, and checked it with screen readers. Automated tests (in the tests folder) also check how it works.

## Running

Tested on Windows with Python 3.14.

```
python -m pip install -r requirements.txt
python -X utf8 app/main.py
```

Saved data goes in the data folder.

## Tests

The tests run without speech and without showing windows. The publishing tests only talk to a local test server.

```
python -X utf8 tests/test_domain.py
python -X utf8 tests/test_publish.py
python -X utf8 tests/smoke_app.py
```

The results are written to the tests folder. Exit code 0 means everything passed.

## Building the exe

Build in a new virtual environment with only requirements.txt installed. If other packages are installed, they can end up in the exe even though they are not used.

```
python -m PyInstaller --noconfirm --clean FGTournament_v1.1.0.spec
```

## Setting up a server for online publishing

To use the viewer page, you need your own server that runs PHP. This software does not come with a server to connect to.

1. Open tournament_relay.php in the web folder. On the `__ADMIN_KEY__` line, replace `__ADMIN_KEY__` with a long string that is hard to guess (the admin key)
2. Put tournament_relay.php and viewer.html in the same folder on your server. Use a place that opens over HTTPS (the app only accepts URLs that start with `https://`)
3. Make that folder writable. Tournament data is saved in a folder named t inside it
4. Turn off directory listing on the server, so the list of files in the t folder cannot be seen. The string in a viewer link is the same as the file name
5. In the app, open Publish Settings on the Publish menu and enter the URL of tournament_relay.php and the admin key

Do not put a copy of tournament_relay.php that contains your admin key in a public place.

### If your server was set up for an earlier version

Replace viewer.html and tournament_relay.php on your server with the ones from this version.

- viewer.html: needed to show the viewer page in the app's language (English / Japanese). With the earlier one, the buttons and headings stay in Japanese even when you publish in English. It contains no admin key, so you can simply replace it
- tournament_relay.php: it now accepts up to 2 MB instead of 512 KB, and it does not send the data again when nothing has changed, so spectators download much less. It can also delete the data of an old viewer link when you reissue the link in the app. When you replace it, write the same admin key in place of `__ADMIN_KEY__` again

Publishing also works with the earlier tournament_relay.php. A finished 64-player round robin league is about 300 KB. With very long player names (about 20 full-width characters), it can go over the 512 KB limit.

Your web server also has its own upload size limit (for example nginx's client_max_body_size). If uploads still fail with "too large" when using the new tournament_relay.php, check that setting.

## License

Distributed under the MIT License (Copyright (c) 2026 Takahashi). The full text is in [LICENSE.txt](LICENSE.txt).

The zip on the Releases page includes THIRD-PARTY-NOTICES.txt, with the full license texts of the components bundled in the exe.

## Folders

- `app`: windows and speech. The entry point is `app/main.py`
- `domain`: tournament progress, pairings, standings and the spoken text (does not depend on the windows or on speech)
- `i18n`: the text for the screen and for speech (`ja.py` is Japanese, `en.py` is English; both use the same keys)
- `publish`: building and sending the data for the viewer page
- `web`: the viewer page and the PHP relay that goes on the server
- `tests`: tests
