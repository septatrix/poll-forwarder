# poll-forwarder

A Telegram bot that forwards polls from one chat to one or more target chats,
replying to each forwarded poll with a link back to the original message.

## Installation

Requires Python 3.14 or newer.

```sh
pip install .
```

For development (adds `mypy` and `ruff`):

```sh
pip install -e '.[dev]'
```

## Usage

```sh
poll-forwarder --token-file .token --config config.json
```

- `--token-file` (required):
  path to a file containing the Telegram bot token.
- `--config` (default `config.json`):
  path to the forwarding configuration.

The configuration maps each source chat ID to a list of target chat IDs:

```json
{
  "-1001234567890": [-1009876543210, -1005555555555]
}
```
