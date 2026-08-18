PAGE_METADATA = {
    "HOME_PAGE": "Data on homepage",
    "PARTNERS":"Partners",
    "NETWORK_MAP":"Network Map",
    "STATE_DETAILS": "States details",
    "STATE_DISTRICT_DETAILS": "State_district details",
    "GOALS":"Goals",
    "DASHBOARD_FIRST_PAGE":"Dashboard first page",
    "TESTIMONIALS":"Testimonials",
    "PROGRAMS":"Programs",
    "COMMUNITY_LED_PROGRAMS":"Community Led Programs",
    "DISTRICT_DETAILS": "District Details",
    "UPLOAD_IMAGES":"Imagesicons",
    "VOICE_TAB_BIG_NUMBERS":"Voices tab_Big numbers",
    "NEW_COMMUNITY_LED_PROGRAMS":"New_Community led Programs "
    }

TABS_METADATA = {
    "HOME_PAGE":['Indicator', 'Definition', 'Data', 'Icon link'],
    "PARTNERS":['Name of the Partner','Logo of the partner','Country of the partner','State in which  the partner is present', 'Type of the Partner','Website', 'lattitude','longitude'],
    "NETWORK_MAP":['Source Partner','Source Partner State','Source partner country','Target Partner','Target Partner state','Target partner country'],
    "STATE_DETAILS": ["State Name", "Indicator", "Definition", "Data"],
    "STATE_DISTRICT_DETAILS": ["state name", "district name", "state code", "district code"],
    "GOALS":["Indicator","Data"],
    "PIE_CHART":["Indicator","Definition","Data"],
    "TESTIMONIALS":['Name of the Partner', 'Testimonial ( waht partners have to say about the movement, network, etc.)', 'Name of the person', 'Designation', 'Image of the person'],
    "PROGRAMS": [
        "State Name",
        "District Name",
        "Program Type",
        "Name of the Program",
        "About the Program/ Objective",
        "Impact of the program",
        "Learn how Micro improvements are contributing to mega impact  ( Impact achieved)",
        "Download to read more",
        "Stakeholders doing the program",
        "Pictures from the program",
        "MI inititated from the program ( Total no. of MI started+inprogress+submitted OR if done via google form then no. of responses submitted)",
        "Leaders Driving Improvements",
        "Status of the program",
        "Name of the Partner leading the program",
        "Report Link"
    ],
    "COMMUNITY_LEAD_PROGRAMS":["Name of the State ","Name of the District","No. of community leaders engaged","Community led improvements","Challenges shared","Solutions shared","Infrastructure and resources","School structure and practices","Leadership"," Pedagogy","Assessment and Evaluation","Community Engagement","Districts initiated"],
    "DISTRICT_DETAILS": ["State Name", "District Name", "Indicator", "Definition", "Data"],
    "UPLOAD_IMAGES":['Name of images','Link of images'],
    "VOICE_TAB_BIG_NUMBERS":['Name of the State ','Name of the District', 'Shiksha Chaupals', 'Community members participating in dialogues', 'Local challenges identified', 'Community leaders driving improvements', 'Local solutions identified', 'Local Solutions implemented'],
    "NEW_COMMUNITY_LED_PROGRAMS" :["Name of the State ","Name of the District","Community members participating in dialogues","Local challenges identified","Community leaders driving improvements","Local solutions identified","Local Solutions implemented","Community Engagement","Infrastructure and resources","School structure and practices","Leadership"," Pedagogy ","Assessment and Evaluation","Districts initiated"]
}


ALLOWED_TABS = [
    "Data on homepage", "Dashboard first page", "Goals", "States details",
    "District Details", "Programs", "Micro improvements progress",
    "Graph_VoiceTab_MI", "Partners", "Network Map", "Testimonials",
    "Images/icons", "Voices Tab Big Numbers"
]

BUCKET_PREFIX_FOR_IMAGES= "sg-dashboard/assets/icons/"

GCS_STORAGE_BASE_URL = "https://storage.googleapis.com"

GCS_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

DRIVE_IMAGE_URL = "https://drive.google.com/uc?export=view&id="

DRIVE_DOWNLOAD_URL = "https://drive.google.com/uc?export=download&id="

TOTAL_ROW_LABEL = "total"

COMMUNITY_STATE_COLUMN = "Name of the State"
COMMUNITY_DISTRICT_COLUMN = "Name of the District"

COMMUNITY_MAP_COLUMNS = [
    "Community members participating in dialogues",
    "Local challenges identified",
    "Community leaders driving improvements",
    "Local solutions identified",
    "Local Solutions implemented",
]

COMMUNITY_PIE_COLUMNS = [
    "Infrastructure and resources",
    "School structure and practices",
    "Leadership",
    "Pedagogy",
    "Assessment and Evaluation",
    "Community Engagement",
]

COMMUNITY_PIE_DISPLAY_NAMES = {
    "Infrastructure and resources": "Infrastructure and Resources",
    "School structure and practices": "School Structure and Practices",
    "Leadership": "Leadership",
    "Pedagogy": "Pedagogy",
    "Assessment and Evaluation": "Assessment and Evaluation",
    "Community Engagement": "Community Engagement",
}

CHILDREN_ENROLLED = "Children enrolled"
ATRISK_CHILDREN_REGULARISED = "At-risk of dropout children regularised in school"
CHILDREN_GOT_AADHAAR = "Children who got Aadhaar"
CHILDREN_GOT_BIRTH_CERTIFICATE = "Children who got Birth Certificate"
DISTRICTS_ACTIVATED = "Districts activated"

ENROLLMENT_COLUMNS = {
    "state": 1,
    "district": 2,
    "children_total": 6,
    "atrisk_total": 10,
}

DOCUMENTATION_COLUMNS = {
    "state": 0,
    "district": 1,
    "aadhaar": 2,
    "birth_certificate": 3,
}

COMMUNITY_COUNTRY_VIEW_CODE_ORDER = (
    COMMUNITY_MAP_COLUMNS
    + [
        DISTRICTS_ACTIVATED,
        CHILDREN_ENROLLED,
        ATRISK_CHILDREN_REGULARISED,
        CHILDREN_GOT_AADHAAR,
        CHILDREN_GOT_BIRTH_CERTIFICATE,
    ]
)

COMMUNITY_DETAILS_REQUIRED_COLUMNS = [
    COMMUNITY_STATE_COLUMN,
    COMMUNITY_DISTRICT_COLUMN,
    *COMMUNITY_MAP_COLUMNS,
    "Community Engagement",
    "Infrastructure and resources",
    "School structure and practices",
    "Leadership",
    "Pedagogy",
    "Assessment and Evaluation",
    "Districts initiated",
]

COMMUNITY_DISTRICT_METRICS = [
    (CHILDREN_ENROLLED, 6),
    (ATRISK_CHILDREN_REGULARISED, 7),
    (CHILDREN_GOT_AADHAAR, 8),
    (CHILDREN_GOT_BIRTH_CERTIFICATE, 9),
]

LINE_CHART_QUARTERS = ["Q1", "Q2", "Q3", "Q4"]

LINE_CHART_DEFAULT_INDEXES = {
    "state": 0,
    "district": 1,
    "metric": 2,
    "year": 2,
    "leaders_year": 3,
    "quarter_start": 3,
    "leaders_quarter_start": 4,
}

LEADERS_ENGAGED_SHEET = "Leaders and community members_v"
VOICE_MICRO_IMPROVEMENTS_SHEET = "Graph_VoiceTab_MI"
MICRO_IMPROVEMENTS_PROGRESS_SHEET = "Micro improvements progress"
LEADING_MICRO_IMPROVEMENTS = "Leading Micro Improvements"
PARTICIPATING_IN_DIALOGUES = "Participating in dialogues"
LEADERS_ENGAGED_METRICS = [
    LEADING_MICRO_IMPROVEMENTS,
    PARTICIPATING_IN_DIALOGUES,
]

ANIMATIONS_REQUIRED_COLUMNS = ['challenge_id', 'challenge_user_role', 'challenge_district', 'challenge_state', 'challenge', 'add_to_frontend', 'solution_id', 'solutions', 'solution_user_role', 'solution_district', 'solution_state']

ANIMATIONS_MANDATORY_OUTPUT_COLUMNS = ['challenge_id', 'challenge_user_role', 'challenge_district', 'challenge_state', 'challenge','solution_id', 'solutions', 'solution_user_role', 'solution_district', 'solution_state']

FEEDS_REQUIRED_COLUMNS = ['story_id', 'action_steps', 'impact', 'add_to_frontend', 'pii_flag', 'role', 'district', 'state', 'justification', 'confidence_score']

FEEDS_MANDATORY_OUTPUT_COLUMNS = ['action_steps', 'impact', 'pii_flag', 'role', 'district', 'state']

STORIES_REQUIRED_COLUMNS = ['story_id', 'story_title', 'updated_story_title', 'content', 'pdf_link', 'document_language', 'composite_score', 'add_to_frontend', 'overall_summary', 'image_link', 'role', 'district', 'state', 'translated_title', 'translated_content', 'translated_role', 'translated_district', 'translated_state']

STORIES_MANDATORY_OUTPUT_COLUMNS = ['story_title', 'content', 'pdf_link', 'document_language', 'role', 'district', 'state']

THEMES_REQUIRED_COLUMNS = ['discussion_id', 'challenge', 'theme_name', 'add_to_frontend', 'theme_id', 'pii_flag', 'role', 'district', 'state', 'confidence_score', 'justification']

THEMES_MANDATORY_OUTPUT_COLUMNS = ['challenge', 'theme_name', 'theme_id', 'pii_flag', 'role', 'district', 'state']
