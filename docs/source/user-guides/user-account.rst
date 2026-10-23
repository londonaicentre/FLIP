..  Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

.. _user-account:

######################
Account and sign-in
######################

How to get a FLIP account, sign in for the first time and look after your password.

.. _request-access:

**************
Request access
**************

You can ask for an account from the login page. You do not need an account to send the request.

1. Click **Request access** beneath the **Log In** button.
2. Enter your email address, full name and why you need access.
3. Click **Submit your request**.

An administrator reviews the request in the Admin Area and either enrols you, choosing your role, or dismisses the
request (see :ref:`admin-project-and-user-management`). Your request is saved even if the notification email to the
administrators fails, so it is not lost.

When you are enrolled, FLIP emails you a one-time password for your :ref:`initial-login`.

.. tip::

   You can also ask your local FLIP system administrator or information asset owner (IAO) directly. Give them your
   email address and the role you need (see :ref:`rbac-roles`).

.. _initial-login:

*************
Initial login
*************

1. Enter your email address and the one-time password from your enrolment email.
2. Click **Log In**.
3. Set a new password.
4. Set up multi-factor authentication (MFA): scan the QR code with an authenticator app (Google Authenticator, Authy,
   1Password, etc.) and enter the 6-digit code it shows.

.. note::

   Your password needs at least 8 characters, including upper- and lower-case letters, a number and a special
   character such as ``@``, ``#``, ``&``, ``!`` or ``?``.

.. important::

   **MFA enrolment is mandatory** and cannot be postponed. Until you finish it, every page redirects you to the
   enrolment page; only sign-in, the password pages, the access request form, the privacy policy and the terms of
   service stay reachable.

   Keep the authenticator entry: you need a fresh code from it every time you sign in. If you lose the device, an
   administrator can reset your MFA so you can enrol again (see :ref:`admin-project-and-user-management`).

.. note::

   Local development deployments set ``ENFORCE_MFA=false``, which skips enrolment and the code prompt. Staging and
   production enforce MFA by default and turn it off only deliberately, for testing.

.. figure:: ../assets/generated/gifs/flip/flip-first-login.gif
   :width: 600
   :align: center

   Logging into FLIP for the first time.

.. _flip-login:

Signing in
==========

On later visits, sign in with your email address and password, then enter the current 6-digit code from your
authenticator app. You land on the Projects page.

.. figure:: ../assets/generated/gifs/flip/flip-login.gif
   :width: 600
   :align: center

   Signing in to FLIP.

.. _forgot-password:

***************
Forgot password
***************

1. Click **Forgot password?** on the login page.
2. Enter the email address of your FLIP account.
3. Click **Request Code**.
4. When the confirmation code arrives by email, enter it with a new password.
5. Click **Change Password**.

.. figure:: ../assets/generated/gifs/flip/forgot-password.gif
   :width: 600
   :align: center

   Resetting a forgotten password.

.. note::

   Your local FLIP system administrator can also start the reset for you. You then receive the confirmation code by
   email without asking for one: click **I have a code** instead of **Request Code** in step 3.

.. _change-password:

***************
Change password
***************

While signed in, you can change your password at any time.

1. Open the account menu in the top right-hand corner.
2. Click **Change Password**.
3. Click **Request Code** to have a confirmation code emailed to you.
4. Enter the confirmation code and a new password.
5. Click **Change Password**.

.. figure:: ../assets/generated/gifs/flip/change-password-user.gif
   :width: 600
   :align: center

   Changing your password while signed in.

.. _dark-mode:

*********
Dark mode
*********

FLIP has light and dark display modes, and remembers your choice between sessions.

1. Open the account menu in the top right-hand corner.
2. Click **Dark Mode** to switch to the dark theme; the same item reads **Light Mode** while dark mode is on.

.. figure:: ../assets/generated/gifs/flip/dark-mode.gif
   :width: 600
   :align: center

   Switching between light and dark mode.
