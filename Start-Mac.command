#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 video_feedback.py "$@" || read -r -p "Press Enter to close "
