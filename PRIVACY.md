# Privacy Policy: Team 4201 Match Uploader

Last updated: October 24, 2026

Team 4201 Match Uploader is an internal tool used by FRC Team 4201 (the Vitruvian Bots) to cut robot match videos and upload them to the team's own YouTube channels, such as Team 4201 and SoCal Showdown. It is run only by team members on a team computer. It is not offered to the public.

## What the tool accesses

When a team member signs in with Google, the tool asks for permission to:

- upload videos to the YouTube channel they choose, and
- read the channel's ID and name and manage its playlists, so it can confirm it is uploading to the right channel and add each video to the event's playlist.

The tool does not read email, contacts, files, or any other Google data.

## What is stored, and where

The sign-in token Google returns is saved only on the team computer that runs the tool, in one file per channel. The tool keeps a local log of uploads (file names, match times, and YouTube video links). Nothing is sent to any server other than Google's and YouTube's own APIs.

No data is sold, shared with third parties, or used for advertising.

## Removing access

A team member can remove a saved sign-in on the team computer with `python split_and_upload.py --config <config> --sign_out`, and can revoke the tool's access at any time from https://myaccount.google.com/permissions.

The tool's use of information received from Google APIs follows the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements. Uploads are also subject to the [YouTube Terms of Service](https://www.youtube.com/t/terms) and the [Google Privacy Policy](https://policies.google.com/privacy).

## Contact

Questions: open an issue at https://github.com/4201VitruvianBots/AV-Automation/issues.
