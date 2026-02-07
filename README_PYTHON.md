# Discord X Verif (Python Version)

This is a Python rewrite of the original Node.js bot. It runs as a single process and requires no Docker or Redis.

## Setup

1.  **Install Python 3.8+**
2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **Configure Environment**:
    - Create a `.env` file in this directory (you can copy `.env.example` as a base, but we only need two variables now).
    - Add your bot token and channel name:
      ```env
      DISCORD_TOKEN=your_actual_bot_token_here
      VERIFY_CHANNEL=verify
      ```
4.  **Run the Bot**:
    ```bash
    python bot.py
    ```

## Features

- **X (Twitter) Link Validation**: Ensures the link matches the format and the user matches the claimed `@handle`.
- **Image Requirement**: Enforces exactly one image attachment.
- **Queue System**: Uses an internal memory queue to process verifications sequentially (simulating the original worker logic).
- **Role Assignment**: Assigns "Project A/B/C/D" roles based on a deterministic hash of the user ID + Link.

## Notes
- Since Redis is removed, restarting the bot will clear the current verification queue.
