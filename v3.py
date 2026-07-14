import pandas as pd
import requests
import json

url = "http://13.204.234.240:8093/jobAuditDetails"

tables = pd.read_html(url)

# The page has several small status-widget tables before the actual job table,
# so find it by its columns instead of assuming a fixed index.
job_table = None
for t in tables:
    if {'App Name', 'Job Name', 'Started (IST)'}.issubset(t.columns):
        job_table = t
        break

if job_table is None:
    raise ValueError("Could not find the job audit table on the page (columns not matched).")

job_table['Started (IST)'] = pd.to_datetime(job_table['Started (IST)'], errors='coerce')
job_table['Ended (IST)'] = pd.to_datetime(job_table['Ended (IST)'], errors='coerce')  # NaT for jobs still running ('-')

now = pd.Timestamp.now(tz='Asia/Kolkata').tz_localize(None)
last_24_hours = now - pd.Timedelta(hours=24)

# --- Get latest run per job ---
latest_runs = job_table.loc[
    job_table.groupby(['App Name', 'Job Name'])['Started (IST)'].idxmax()
][['App Name', 'Job Name', 'Started (IST)', 'Ended (IST)', 'Duration', 'Status', 'Error Message']].reset_index(drop=True)


shopify_running = latest_runs[
    (latest_runs['Job Name'] == 'ShopifyStoreProductDumpScheduler') &
    (latest_runs['Status'] == 'Running')
].copy()

# --- 2) Stale jobs: last run was before 24 hours ago (exclude Shopify already caught above) ---
stale_jobs = latest_runs[
    (latest_runs['Started (IST)'] < last_24_hours) &
    (latest_runs['Job Name'] != 'ShopifyStoreProductDumpScheduler')
].copy()
stale_jobs['Status'] = 'Stale (Not run in 1+ days)'

# --- 3) Error jobs: latest run in last 24h ended in Error ---
error_jobs = latest_runs[
    (latest_runs['Status'] == 'Error') &
    (latest_runs['Started (IST)'] >= last_24_hours) &
    (latest_runs['Job Name'] != 'ShopifyStoreProductDumpScheduler')
].copy()

# --- Also check Shopify for stale / error if it's NOT running ---
shopify_latest = latest_runs[latest_runs['Job Name'] == 'ShopifyStoreProductDumpScheduler']

if not shopify_latest.empty and shopify_latest.iloc[0]['Status'] != 'Running':
    shopify_row_df = shopify_latest.iloc[[0]].copy()

    if shopify_latest.iloc[0]['Started (IST)'] < last_24_hours:
        shopify_row_df['Status'] = 'Stale (Not run in 1+ days)'
        stale_jobs = pd.concat([stale_jobs, shopify_row_df], ignore_index=True)

    elif shopify_latest.iloc[0]['Status'] == 'Error':
        error_jobs = pd.concat([error_jobs, shopify_row_df], ignore_index=True)

all_flagged = pd.concat([shopify_running, stale_jobs, error_jobs], ignore_index=True)

if not all_flagged.empty:
    message_text = f"⚠️ {len(all_flagged)} Jobs Flagged**\nHello Team, /jobs\n\n"

    if not shopify_running.empty:
        shopify_running = shopify_running.copy()
        shopify_running['Started (IST)'] = shopify_running['Started (IST)'].dt.strftime('%Y-%m-%d %H:%M:%S')
        message_text += "🔵 ShopifyStoreProductDumpScheduler Currently Running:\n"
        for _, row in shopify_running.iterrows():
            message_text += (
                f"• App: {row['App Name']}\n  Job: {row['Job Name']}\n"
                f"  Since: {row['Started (IST)']}\n  Running For: {row['Duration']}\n\n"
            )

    if not stale_jobs.empty:
        stale_jobs = stale_jobs.copy()
        stale_jobs['Started (IST)'] = stale_jobs['Started (IST)'].dt.strftime('%Y-%m-%d %H:%M:%S')
        message_text += f"🟡 {len(stale_jobs)} Stale Job(s) (Not run in 1+ days):\n"
        for _, row in stale_jobs.iterrows():
            message_text += (
                f"• App: {row['App Name']}\n  Job: {row['Job Name']}\n"
                f"  Last Run: {row['Started (IST)']}\n  Duration: {row['Duration']}\n"
                f"  Status: {row['Status']}\n\n"
            )

    if not error_jobs.empty:
        error_jobs = error_jobs.copy()
        error_jobs['Started (IST)'] = error_jobs['Started (IST)'].dt.strftime('%Y-%m-%d %H:%M:%S')
        message_text += f"🔴 {len(error_jobs)} Error Job(s):\n"
        for _, row in error_jobs.iterrows():
            error_msg = row['Error Message'] if pd.notna(row['Error Message']) and str(row['Error Message']).strip() else 'N/A'
            message_text += (
                f"• App: {row['App Name']}\n  Job: {row['Job Name']}\n"
                f"  Time: {row['Started (IST)']}\n  Duration: {row['Duration']}\n"
                f"  Status: {row['Status']}\n  Error: {error_msg}\n\n"
            )

    lark_webhook_url = "https://open.larksuite.com/open-apis/bot/v2/hook/74608033-96fb-42bf-878f-03f7b03db14e"
    payload = {
        "msg_type": "text",
        "content": {"text": message_text}
    }
    headers = {'Content-Type': 'application/json'}
    response = requests.post(lark_webhook_url, headers=headers, data=json.dumps(payload))
    if response.status_code == 200:
        print("Successfully sent message to Lark!")
    else:
        print(f"Failed to send to Lark. Error: {response.text}")

else:
    print("All jobs are healthy! No message sent.")