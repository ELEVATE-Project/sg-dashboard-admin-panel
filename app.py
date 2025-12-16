import streamlit as st
import pandas as pd
from tabs_scripts.community_led_details import community_led_programs_sum_with_codes, pie_chart_community_led
from tabs_scripts.goals import goals  # Needed if you're working with CSV or Excel
from tabs_scripts.key_progress_indicators import key_progress_indicators
from tabs_scripts.line_chart import extract_district_line_chart, extract_micro_improvements, extract_state_line_chart
from tabs_scripts.network_map_data import get_network_map_data
from tabs_scripts.partners import get_partners
from tabs_scripts.extract_state_details import update_district_view_indicators
from tabs_scripts.pie_chart import pie_chart
from tabs_scripts.testimonials import testimonials
from tabs_scripts.programs import generate_program_reports
from tabs_scripts.extract_district_details import extract_district_details
from tabs_scripts.extract_community_details import extract_community_details

# Page setup
st.set_page_config(page_title="File Upload App", page_icon=":page_facing_up:")

# Title and logo
st.title("File Upload App")
st.image("main_logo.svg", caption="Shikshagraha Dashboard", use_column_width=True)  # Make sure logo.png is in the same folder or provide correct path

# File uploader
uploaded_file = st.file_uploader("Choose a file", type=["csv", "txt", "xlsx"])

# 🔽 Step 5: Add the below block immediately after the file_uploader
if uploaded_file is not None:
    st.success("✅ File uploaded successfully!")

    # Example: Process CSV file
    try:
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        elif uploaded_file.name.endswith('.xlsx'):
            key_progress_indicators(uploaded_file) 
            get_partners(uploaded_file)
            get_network_map_data(uploaded_file)
            update_district_view_indicators(uploaded_file)
            extract_district_details(uploaded_file)
            goals(uploaded_file)
            pie_chart(uploaded_file)
            testimonials(uploaded_file)
            pie_chart_community_led(uploaded_file)
            community_led_programs_sum_with_codes(uploaded_file)
            generate_program_reports(uploaded_file)
            extract_community_details(uploaded_file)
            extract_micro_improvements(uploaded_file)
            df = pd.read_excel(uploaded_file)
        elif uploaded_file.name.endswith('.txt'):
            df = pd.read_csv(uploaded_file, delimiter="	")
        else:
            st.error("Unsupported file format.")
            st.stop()

        # Show preview
        st.subheader("Preview of uploaded data")
        st.write(df.head())

        # 🔁 Run your custom script here
        # result = your_script_function(df)
        # st.write("Result of script:")
        # st.write(result)

    except Exception as e:
        st.error(f"Error processing file: {e}")
