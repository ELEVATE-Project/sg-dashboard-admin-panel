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
    "VOICE_TAB_BIG_NUMBERS":"Voices tab_Big numbers"
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
        "Stakeholders doing the program",
        "Pictures from the program",
        "MI inititated from the program ( Total no. of MI started+inprogress+submitted OR if done via google form then no. of responses submitted)",
        "Status of the program",
        "Name of the Partner leading the program",
        "Report Link"
    ],
    "COMMUNITY_LEAD_PROGRAMS":["Name of the State ","Name of the District","No. of community leaders engaged","Community led improvements","Challenges shared","Solutions shared","Infrastructure and resources","School structure and practices","Leadership"," Pedagogy","Assessment and Evaluation","Community Engagement","Districts initiated"],
    "DISTRICT_DETAILS": ["State Name", "District Name", "Indicator", "Definition", "Data"],
    "UPLOAD_IMAGES":['Name of images','Link of images'],
    "VOICE_TAB_BIG_NUMBERS":['Name of the State ','Name of the District', 'Shiksha Chaupals', 'Community members participating in dialogues', 'Local challenges identified', 'Community leaders driving improvements', 'Local solutions identified', 'Local Solutions implemented']
}


ALLOWED_TABS = [
    "Data on homepage", "Dashboard first page", "Goals", "States details",
    "District Details", "Programs", "Micro improvements progress",
    "Partners", "Network Map", "Testimonials", "Images/icons", "Voices Tab Big Numbers"
]

BUCKET_PREFIX_FOR_IMAGES= "sg-dashboard/assets/icons/"

DRIVE_IMAGE_URL = "https://drive.google.com/uc?export=view&id="

DRIVE_DOWNLOAD_URL = "https://drive.google.com/uc?export=download&id="