# High-Level Implementation Plan: Shopee Auto-Reply Bot Service

## Overview
This project aims to containerize and daemonize the existing Shopee auto-reply script (Python/Playwright) into a robust, standalone service. The bot will use a dedicated browser profile with persistent sessions to avoid repeated logins, and will be easily manageable and deployable using Docker.

## Objectives
1. **Dedicated Browser Profile**: Isolate the bot's browser session from daily use to prevent conflicts.
2. **Session Persistence**: Maintain the login state across container restarts or script crashes.
3. **Containerization**: Package the bot with all necessary dependencies (Chromium, Playwright) using Docker.
4. **Daemonization**: Run the bot as a reliable background service with automatic restart capabilities and robust logging.
5. **Remote Intervention**: Provide a mechanism for manual interaction (e.g., initial login, solving captchas) in a headless server environment.

## Architecture & Setup Guidelines

### 1. Script Modifications (Python / Playwright)
- **Persistent Context**: Update the Playwright initialization to use `launch_persistent_context`. Set the `user_data_dir` to a designated path (e.g., `/data/shopee-profile`).
- **Daemon Loop**: Wrap the core logic in a continuous loop. Use polling or network interception to detect new incoming chats.
- **Robust Logging**: Implement structured logging to output info and errors. This is critical for monitoring when Shopee changes its DOM structure, causing the bot to break.

### 2. Docker Configuration (`Dockerfile`)
- **Base Image**: Utilize the official Playwright Python image (e.g., `mcr.microsoft.com/playwright/python:vX.XX.X-jammy`) to ensure Chromium and all system dependencies are pre-installed.
- **Setup**: Copy the application files, install Python dependencies, and define the start command.

### 3. Service Orchestration (`docker-compose.yml`)
Construct a Docker Compose file to manage the service deployment:
- **Volume Mounts**: Crucially, map a host directory to the container's profile directory (e.g., `./bot-profile:/data/shopee-profile`). This persists the session data outside the container's lifecycle.
- **Restart Policy**: Implement `restart: unless-stopped` to ensure the service acts as a daemon, automatically restarting if the Python script crashes or the server reboots.

### 4. Handling Manual Login & Captchas (VNC/UI)
To handle the initial manual login on a headless server:
- **Option A (Pre-generation)**: Run the script locally in headful mode, perform the login, and then transfer the generated profile directory to the server before starting the Docker service.
- **Option B (VNC/X11 Integration)**: Embed a virtual display (`Xvfb`) and a VNC server (`x11vnc` or noVNC) into the Docker container. Expose the VNC port in `docker-compose.yml` so an admin can connect via a VNC viewer/browser to perform the initial login directly on the server.

## Next Steps for Implementation
1. Refactor the existing Python script for persistent contexts and continuous execution.
2. Write the `Dockerfile`.
3. Write the `docker-compose.yml` with appropriate volume mounts and restart policies.
4. Document the exact steps for performing the initial manual login.
