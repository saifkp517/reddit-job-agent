Paste everything below the line into Claude Sonnet (effort: high, web search on).

---

I'm building a bot that watches Reddit for hiring posts and emails applications to the ones that give a contact email. I need you to research which subreddits and keywords to use. Search the web and Reddit itself; don't answer from memory.

**About me (who the bot applies for):**
- Full-stack developer (Node.js, NestJS, React/Next.js, TypeScript, PostgreSQL, MongoDB, Docker, VPS deploys), plus Python/RAG/AI agents and CRM automation (Zoho, webhooks, sync pipelines).
- Also open to generalist / founder's-associate / ops / growth roles at early-stage startups, where I'd mix outreach, research and ops with automation and small tech tasks.
- Based in Bangalore, India (IST). Remote only. Full-time, part-time, or contract.

**What I need:**

1. **Subreddits.** 15–25 subreddits where people actively post jobs or paid gigs that fit the profile above. For each one give:
   - name (r/...)
   - roughly how many hiring posts per day or week (check recent posts, not just member count)
   - whether posts usually include an email address, or mostly say "DM me" / link to a form. This matters most: my bot can only act on posts with an email.
   - whether remote / worldwide applicants are welcome, or it's mostly US-only
   - its title-tag convention, e.g. "[Hiring]" vs "[For Hire]"
   - any rule against automated or bulk applications
   Rank them by how useful they are to me. Drop dead subreddits (no hiring posts in the last 30 days).

2. **Include keywords.** Words or phrases in the title/body that mean "someone is hiring", e.g. `[hiring]`, `we're hiring`, `looking for a developer`. Split them into: strong signals (title tags) and weaker signals (body phrases).

3. **Exclude keywords.** Phrases that mean I should skip the post: people offering their services (`[for hire]`), unpaid / equity-only / "rev share", US-citizens-only, on-site only, and common scam patterns (e.g. "telegram", "crypto investment", "pay a fee").

4. **Email patterns.** How people write emails in these posts, including obfuscated forms like `name [at] company dot com`, so I can match them with a regex.

**Output format.** End your answer with this block filled in so I can paste it straight into my config:

```
SUBREDDITS=sub1+sub2+sub3
INCLUDE_KEYWORDS=keyword one,keyword two
EXCLUDE_KEYWORDS=keyword one,keyword two
```

Keep keywords lowercase, comma-separated, no quotes.
