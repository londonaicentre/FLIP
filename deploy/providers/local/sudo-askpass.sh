#!/bin/sh
# sudo-askpass.sh — used by Ansible's become -A flag on local connections.
# sudo-rs does not support Ansible's interactive become prompt detection;
# this script provides the password via SUDO_ASKPASS instead.
#
# Usage: set ANSIBLE_BECOME_PASS before invoking ansible-playbook, then
# point SUDO_ASKPASS at this script and pass -e ansible_become_flags=-A.
printf '%s\n' "$ANSIBLE_BECOME_PASS"
