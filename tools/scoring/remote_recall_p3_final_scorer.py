#!/usr/bin/env python3
from __future__ import annotations

import remote_candidate_source_final_scorer as scorer

scorer.GENERAL_JOB_PLATFORMS.update({
    "themuse.com",
    "powertofly.com",
    "cv-library.co.uk",
})

scorer.MARKETPLACES.update({
    "fiverr.com",
    "gun.io",
    "cloudpeeps.com",
})

scorer.RECRUITERS.update({
    "intuition-it.com",
    "gibbshybrid.com",
    "infinityquest.co.uk",
    "robertwalters.com",
    "trilogyinternational.com",
    "forbesprojectsolutions.com",
})

scorer.EMPLOYER_CAREERS.update({
    "eurodyn.com",
    "outreach.io",
    "workwave.com",
    "accesa.eu",
    "citrix.com",
    "thrivedigital.com",
})

scorer.CONTENT_OR_LOW_DIRECTNESS.update({
    "hireez.com",
    "business-umbrella.com",
})

scorer.KNOWN_HIGH_VALUE_WITH_WEAK_PROFILE.update({
    "themuse.com",
    "powertofly.com",
    "cv-library.co.uk",
    "fiverr.com",
    "gun.io",
    "cloudpeeps.com",
    "robertwalters.com",
})

if __name__ == "__main__":
    raise SystemExit(scorer.main())
