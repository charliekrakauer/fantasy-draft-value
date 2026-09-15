# Deploying Value Board to a public website

This is the concrete path from "files on your computer" to "live at your
own domain, found by Googling it." It's written as steps *you* run — I
don't have push access to GitHub or a registrar account from here, so
everything below is copy/paste commands and screenshots-in-words for you
to follow. Ask me if anything doesn't match what you're seeing.

Everything the site actually needs to run is already in `docs/` — that
folder is a complete, working copy of the page. Nothing else in this
project (the scripts, `data/`, `dist/`) is required for the live site to
work; they're what *generate* `docs/index.html`, not part of what gets
served.

## 1. Create a GitHub repository

1. On [github.com](https://github.com), click **New repository**.
2. Name it whatever you like (e.g. `value-board`). **Public** — GitHub
   Pages' free tier requires a public repo (a private repo needs a paid
   GitHub plan to use Pages).
3. Don't initialize it with a README/gitignore/license — this project
   already has those; an empty repo avoids a merge conflict on the first push.

## 2. Push this project to it

From inside the `value-board/` folder (unzip the project first if you
haven't):

```bash
cd value-board
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

## 3. Turn on GitHub Pages

1. In your new repo on GitHub: **Settings → Pages** (left sidebar).
2. Under **Build and deployment → Source**, choose **Deploy from a branch**.
3. Under **Branch**, choose **main** and **/docs**, then **Save**.
4. GitHub builds the site (takes ~30–60 seconds) and shows you a URL like
   `https://<your-username>.github.io/<your-repo>/`. Open it and confirm
   the dashboard loads — this confirms the *hosting* half works, before
   touching domains/DNS at all.

## 4. Buy a domain — done ✅

Domain: **fantasydraftvalue.com**. Everything below is written for that
exact domain — nothing left to fill in.

## 5. Point the domain at GitHub Pages

You bought the bare (apex) domain, not a `www.` subdomain, so DNS needs
**four `A` records**, all at the root/`@` host, pointing to GitHub Pages'
fixed IPs:

| Type | Host | Value |
|---|---|---|
| A | `@` | `185.199.108.153` |
| A | `@` | `185.199.109.153` |
| A | `@` | `185.199.110.153` |
| A | `@` | `185.199.111.153` |

Some registrars label the root host `@`, others leave it blank — either
means "the bare domain itself." If your registrar offers an `ALIAS` or
`ANAME` record type instead, that's a simpler equivalent for the same
thing; use it if offered.

Optional but recommended: also add `CNAME www → <your-username>.github.io`
so `www.fantasydraftvalue.com` works and redirects to the bare domain
instead of dead-ending.

**Back in GitHub: Settings → Pages → Custom domain**, enter
`fantasydraftvalue.com` and **Save**. GitHub will:
- Confirm (or recreate) the `docs/CNAME` file in your repo containing that
  domain — it's already there in this project, containing just
  `fantasydraftvalue.com`, so this should just confirm it
- Start checking DNS — this can take anywhere from a couple of minutes to
  a few hours depending on your registrar
- Once DNS checks out, an **Enforce HTTPS** checkbox becomes available on
  the same settings page — turn that on once it appears (GitHub
  provisions the certificate automatically; this can take a bit longer
  still, sometimes up to 24 hours, though it's usually much faster)

## 6. SEO placeholder files — done ✅

`docs/robots.txt` and `docs/sitemap.xml` are already pointed at
`https://fantasydraftvalue.com/` — nothing left to edit there.

## 7. Tell Google the site exists

Being reachable and being *findable by search* are different things —
Google doesn't discover a brand-new domain on its own for a while.
[Google Search Console](https://search.google.com/search-console) is
free and speeds this up:

1. Add your domain as a property (it'll ask you to verify ownership,
   usually via a DNS TXT record at your registrar or an HTML file — pick
   whichever your registrar makes easier).
2. Once verified, submit `sitemap.xml` (the full URL:
   `https://fantasydraftvalue.com/sitemap.xml`) under **Sitemaps**.
3. Use **URL Inspection** on your homepage and click **Request Indexing**.

This doesn't guarantee ranking for any particular search term — that
still depends on real traffic and other sites linking to yours over
time — but it's what gets a new site into Google's index at all, usually
within a few days rather than however long it'd take passively.

## Refreshing the live data going forward

Once deployed, updating the public site with new data is: run the
pipeline (which now writes `docs/index.html` automatically, see below),
then push.

```bash
python3 scripts/fetch_current_performance.py --season 2026
python3 scripts/build_dashboard_data.py --season 2026
python3 scripts/build_site.py
git add docs/index.html
git commit -m "Refresh data"
git push
```

GitHub Pages redeploys automatically within about a minute of the push —
no need to touch the Pages settings again after the first setup.

## If you eventually sell this

Keep the GitHub repo, the domain registration, and (if you add one later)
any hosting/analytics accounts under accounts you personally own rather
than anything tied to this Claude conversation — that's what makes the
site an asset you can actually hand off to a buyer (transfer the repo
ownership, transfer the domain registrar account). Nothing in this setup
locks you into GitHub specifically if you outgrow the free tier later —
the `docs/` folder is a plain static site, portable to any static host.
