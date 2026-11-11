This page is the reference for the datasets; the README is the front door.

# Datasets

- Polish companies bankruptcy data, UCI Machine Learning Repository, DOI 10.24432/C5F600, licensed CC BY 4.0.
- Taiwanese bankruptcy prediction, UCI Machine Learning Repository, DOI 10.24432/C5004D, licensed CC BY 4.0.

Both are corporate bankruptcy datasets, not retail credit data, so two proxies are documented and applied:

- **Bankruptcy as default.** The bankruptcy label stands in for the default flag of a PD model.
- **Horizon files as vintages.** The Polish data ships as five files by forecasting horizon (`1year` to `5year`); each file is treated as a vintage, so a held-out file gives an out-of-time split. The Taiwanese set has no time dimension at all, so it uses a stratified random split and every out-of-time test is reported as not applicable.

[Back to the README](../../README.md)
