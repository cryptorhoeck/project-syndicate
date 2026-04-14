# Project Syndicate — Pre-Flight Checklist

# Complete these steps BEFORE opening Claude Code

## 1\. Install Redis/Memurai

* Download Memurai from https://www.memurai.com/get-memurai
* Run the installer (it installs as a Windows service, starts automatically)
* Verify: open CMD and type: redis-cli ping
* Expected response: PONG

## 2\. Create the GitHub repo

* Go to https://github.com/new
* Repo name: project-syndicate
* Make it Private (you can make it public later if agents decide social transparency helps)
* Do NOT initialize with README (we'll push our own)
* Create repository

## 3\. Create the project directory

Open CMD and run:

mkdir "E:\\project syndicate"
cd /d "E:\\project syndicate"
git init
git remote add origin https://github.com/cryptorhoeck/project-syndicate.git

## 4\. Copy files from Claude.ai

* Save the CLAUDE.md file into E:\\project syndicate\\CLAUDE.md
* Save the design doc into E:\\project syndicate\\docs\\Project\_Syndicate\_v2\_Design\_Document.docx
(create the docs folder first: mkdir docs)

## 5\. Verify PostgreSQL

Open CMD and run:

psql -U postgres -c "SELECT version();"

If this works, create the project database:

psql -U postgres -c "CREATE DATABASE syndicate;"

## 6\. Verify Python

python --version
(Should show 3.12 or higher)

## 7\. Verify Node/npm

node --version
npm --version

## 8\. Verify Git config

git config user.name
git config user.email
(Should show your GitHub username and email)

## 9\. Open Claude Code

cd /d "E:\\project syndicate"
claude

## 10\. Paste the kickoff prompt

Copy the contents of CLAUDE\_CODE\_KICKOFF.md and paste it into Claude Code.
Claude Code will read the CLAUDE.md and begin building Phase 0.

## You're live. Good luck, Syndicate.

