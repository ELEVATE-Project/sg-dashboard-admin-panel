import pandas as pd
import streamlit as st

from admin_auth import read_activity


def main():
    st.caption('Shows account, local time (IST), connection IP, browser and operating system, session reference, and upload result when available. Browser and OS are reported by the browser and do not uniquely identify a laptop.')
    events = read_activity()
    if not events:
        st.info('No login or upload activity has been recorded yet.')
        return
    frame = pd.DataFrame(events)
    frame['timestamp'] = pd.to_datetime(frame['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
    users = ['All'] + sorted(frame['username'].dropna().unique().tolist())
    selected_user = st.selectbox('Admin', users, key='_activity_user')
    types = ['All'] + sorted(frame['event'].dropna().unique().tolist())
    selected_type = st.selectbox('Event type', types, key='_activity_type')
    if selected_user != 'All':
        frame = frame[frame['username'] == selected_user]
    if selected_type != 'All':
        frame = frame[frame['event'] == selected_type]
    frame = frame.sort_values('timestamp', ascending=False)
    columns = ['timestamp', 'username', 'event', 'status', 'ip', 'browser',
               'operating_system', 'session_id', 'filename', 'details']
    labels = {'timestamp': 'Time (IST)', 'username': 'Admin', 'event': 'Activity',
              'status': 'Result', 'ip': 'IP address', 'browser': 'Browser',
              'operating_system': 'Operating system', 'session_id': 'Session',
              'filename': 'File', 'details': 'Details'}
    st.dataframe(frame.reindex(columns=columns).rename(columns=labels),
                 hide_index=True, width='stretch')
