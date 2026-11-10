---
id: basel-irb
title: "Basel Framework: Internal Ratings-Based Approach to Credit Risk, PD, LGD and EAD (summary)"
source: "Basel Committee on Banking Supervision; Basel Framework, credit risk standards (CRE30 to CRE36, internal ratings-based approach)"
authority: "Basel Committee on Banking Supervision (Bank for International Settlements)"
date: "2017-12-07"
url: "https://www.bis.org/basel_framework/"
kind: summary
note: "Summary in our own words; none of the Basel Committee's text is reproduced. The date is the publication of Basel III: Finalising post-crisis reforms, which revised the IRB approach. The url is the Basel Framework landing page, from which the CRE chapters are reached."
---

# Basel Internal Ratings-Based Approach: PD, LGD and EAD

## What the approach is

The Basel Committee on Banking Supervision sets international standards for bank capital. Under its credit risk standards, a bank may calculate capital requirements either with the standardised approach, which assigns risk weights from supervisory tables and external ratings, or, with supervisory approval, with the internal ratings-based (IRB) approach, which uses the bank's own estimates of certain risk parameters as inputs to a supervisory risk-weight formula.

The IRB approach was introduced in Basel II in 2004 and is now part of the consolidated Basel Framework in the credit risk chapters. Basel III, finalised in December 2017, revised it, among other things by narrowing which exposure classes may use the most model-dependent version and by introducing or raising floors on several parameters. National implementation differs by jurisdiction and over time; this summary describes the international standard only.

## The risk parameters

- **Probability of default (PD).** The likelihood that a borrower defaults within one year. Under IRB, the PD assigned to a rating grade is a long-run average of one-year default rates for borrowers in that grade, so it is intended to be a through-the-cycle measure rather than a point-in-time one.
- **Loss given default (LGD).** The share of the exposure the bank expects to lose if the borrower defaults, after recoveries. IRB LGD estimates must reflect economic downturn conditions where those are more severe than average.
- **Exposure at default (EAD).** The amount owed at the time of default, including expected drawdowns of undrawn commitments, typically expressed through credit conversion factors.
- **Effective maturity (M).** Used for some exposure classes to adjust the capital requirement for longer-dated exposures.

## Foundation and advanced approaches

Under the foundation IRB approach, a bank estimates PD itself and uses supervisory values for LGD and EAD. Under the advanced IRB approach, the bank also estimates LGD and EAD. For retail exposures there is a single approach in which the bank estimates all three parameters for pools of similar exposures rather than for individual obligors.

## Default definition and data

The Basel default definition treats an obligor as defaulted when the bank considers the obligor unlikely to pay its obligations in full without recourse to actions such as realising collateral, or when the obligor is more than 90 days past due on a material obligation. PD estimates must rest on historical data covering a substantial observation period, and grades must meaningfully differentiate risk.

## Floors

Parameter floors limit how low internal estimates can go. Basel III set the PD floor for corporate exposures at 0.05%, up from 0.03% under Basel II, and introduced floors on LGD and EAD inputs for certain exposures.

## Use of the estimates

IRB banks are expected to use their rating systems and parameter estimates in credit approval, risk management and internal capital allocation, not only for regulatory capital, and to validate them regularly by comparing realised default rates with estimated PDs for each grade.

## Relevance to a PD model

A scorecard that rank-orders borrowers and maps scores to grades is the kind of rating system that underlies an IRB PD estimate, but several further steps would separate a development scorecard from an IRB parameter:

- calibration of each grade to a long-run average default rate rather than to the default rate of the development sample;
- a default definition aligned with the Basel definition (the bankruptcy proxy used in this project is narrower than unlikely-to-pay or 90 days past due);
- application of the supervisory PD floor and documented margins of conservatism;
- supervisory approval of the rating system.

## Use in this corpus

This summary gives the documentation agent context for describing how a PD model's outputs relate to the IRB parameters. It describes the standard in general terms only and makes no claim that any model meets it.
