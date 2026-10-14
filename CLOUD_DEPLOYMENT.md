# GitHub + Streamlit Community Cloud Deployment

This folder is cloud-ready for GitHub user **Analystshaik**.

## Repository

Recommended GitHub repository name:

`Analystshaik/tlt-stock-weekly-pivot-dashboard`

Streamlit entrypoint:

`tlt_weekly_pivot_dashboard.py`

## 1. Prepare the SQLite database

The original desktop project reads:

`C:\Keystone\ibkr_production_data_engine_v2\data\ibkr_market_data.sqlite`

Streamlit Cloud cannot read that Windows path, so the cloud package expects:

`data\streamlit_market_data.sqlite`

Run:

`prepare_cloud_database.bat`

This copies the current desktop database into the repository under the cloud filename. The SQLite file is configured for **Git LFS** by `.gitattributes`.

## 2. Create the GitHub repository

In GitHub, create a new repository named:

`tlt-stock-weekly-pivot-dashboard`

For the simplest first push, create it **empty**: do not add a README, license, or `.gitignore` in GitHub.

## 3. Push the package

Run:

`push_to_github.bat`

It initializes Git, enables Git LFS, tracks `data/*.sqlite`, commits the project, points `origin` at:

`https://github.com/Analystshaik/tlt-stock-weekly-pivot-dashboard.git`

and pushes `main`.

### Verify the SQLite file is in LFS

Run:

`git lfs ls-files`

You should see:

`data/streamlit_market_data.sqlite`

Also run:

`git status`

It should report a clean working tree after the push.

## 4. Deploy in Streamlit Community Cloud

Open Streamlit Community Cloud and create an app with:

- Repository: `Analystshaik/tlt-stock-weekly-pivot-dashboard`
- Branch: `main`
- Main file path: `tlt_weekly_pivot_dashboard.py`
- Python: `3.12`

No secrets are required by this app.

## If GitHub says `fetch first`

That means the remote repository already contains commits. If you do not need those remote starter commits, the cleanest approach is to delete/re-create the GitHub repo as an empty repository and rerun `push_to_github.bat`.

If you need to preserve the remote commits, use:

```bat
git fetch origin
git pull origin main --rebase
git push -u origin main
```

Resolve any rebase conflicts before the final push.

## If Git LFS does not list the database

From the repository folder run:

```bat
git lfs install
git lfs track "data/*.sqlite"
git add .gitattributes
git rm --cached data\streamlit_market_data.sqlite
git add data\streamlit_market_data.sqlite
git commit -m "Track SQLite database with Git LFS"
git push
```

Then confirm with:

```bat
git lfs ls-files
```

## Important data note

The uploaded ZIP did **not** contain the SQLite database itself. The package is therefore ready for deployment, but you must run `prepare_cloud_database.bat` before pushing so the actual market data is included in Git LFS.
