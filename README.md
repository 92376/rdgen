# RDGen, a RustDesk client generator to use with your self-hosted RustDesk server

The client generator is currently hosted [here](https://rdgen.crayoneater.org).
If you would like to host the generator yourself, see [here](setup.md)

## Features

- Embed server and key into client
- Custom app name
- Custom icon/logo
- Set default settings for the client
- Support for rustdesk advanced settings (https://rustdesk.com/docs/en/self-host/client-configuration/advanced-settings/)

## Generate RustDesk clients from command line instead of using a web browser

Save your configuration from the rdgen web interface, or generate your own, then use that json file with [@AlekseyLapunov's rdgen-cli](https://github.com/AlekseyLapunov/rdgen-cli) to build from the command line on Windows, Linux, or MacOS like this: `python rdgen-cli -f my_config.json --set-version 1.4.5 --set-platform windows -s https://rdgen.crayoneater.org`

## Notes

- Icons should be square (256x256 recommended)
- Avoid special characters or non-English characters in app name and file name
- Build time is currently 30 - 45 minutes

## DIY RustDesk 1.4.9 build source

The web form and JSON API dispatch the platform workflow in this repository.
Each workflow checks out the configured DIY RustDesk source before applying the
selected branding and server parameters. The defaults are:

- source repository: `92376/rustdesk-diy`
- source ref: `1.4.9`

Set these environment variables on the Python service when a different fork or
branch should be built:

```text
RUSTDESK_REPOSITORY=92376/rustdesk-diy
RUSTDESK_REF=1.4.9
```

`RUSTDESK_REF` may be left empty to use the RustDesk version selected in the web
form. `GENURL` must be the externally reachable hostname or URL of this service,
because GitHub Actions downloads the encrypted build parameters from it.
