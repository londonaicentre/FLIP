version "6.3"
// FLIP Site-Wide DICOM Anonymization Script
// Compatible with XNAT 1.10.0 (DicomEdit 6.9.x)
//
// This script is applied to all incoming DICOM data when anonymizationEnabled
// is set to true on the SCP receiver. It acts on every attribute in DICOM PS3.15
// Table E.1-1 (2026d) and on every vendor private tag, with no profile options:
// dates, times and patient characteristics go too (OMOP holds them). It is not a
// conformant PS3.15 profile: it removes where the profile empties, and it does
// not check pixel data (FLIP#1513).
//
// Assignments and hashUID act on the top level only. In removals, */(gggg,eeee)
// matches any depth and +/(gggg,eeee) only inside a sequence; X in a tag is any
// hex digit and @ an even one.

// Store XNAT labels into DICOM headers
(0010,4000) := project    // removed again below: Patient Comments is not stored
(0010,0010) := subject
(0010,0020) := subject

// Remove vendor private tags: they routinely repeat names, IDs and dates
removeAllPrivateTags

// Remove nested copies of the labels written above
- +/(0010,0010)    // Patient Name
- +/(0010,0020)    // Patient ID

// Hash UIDs (repeatable, so references between objects still match). DicomEdit only
// assigns at the top level, so nested copies are removed below rather than hashed.
(0004,1511) := hashUID[(0004,1511)]    // Referenced SOP Instance UID in File
(0008,0014) := hashUID[(0008,0014)]    // Instance Creator UID
(0008,0017) := hashUID[(0008,0017)]    // Acquisition UID
(0008,0018) := hashUID[(0008,0018)]    // SOP Instance UID
(0008,0019) := hashUID[(0008,0019)]    // Pyramid UID
(0008,1155) := hashUID[(0008,1155)]    // Referenced SOP Instance UID
(0008,1195) := hashUID[(0008,1195)]    // Transaction UID
(0018,1002) := hashUID[(0018,1002)]    // Device UID
(0018,2042) := hashUID[(0018,2042)]    // Target UID
(0020,000D) := hashUID[(0020,000D)]    // Study Instance UID
(0020,000E) := hashUID[(0020,000E)]    // Series Instance UID
(0020,0052) := hashUID[(0020,0052)]    // Frame of Reference UID
(0020,0200) := hashUID[(0020,0200)]    // Synchronization Frame of Reference UID
(0020,9161) := hashUID[(0020,9161)]    // Concatenation UID
(0020,9164) := hashUID[(0020,9164)]    // Dimension Organization UID
(0028,1199) := hashUID[(0028,1199)]    // Palette Color Lookup Table UID
(0028,1214) := hashUID[(0028,1214)]    // Large Palette Color Lookup Table UID
(003A,0310) := hashUID[(003A,0310)]    // Multiplex Group UID
(0040,0554) := hashUID[(0040,0554)]    // Specimen UID
(0040,4023) := hashUID[(0040,4023)]    // Referenced General Purpose Scheduled Procedure Step Transaction UID
(0040,A124) := hashUID[(0040,A124)]    // UID
(0040,A171) := hashUID[(0040,A171)]    // Observation UID
(0040,A172) := hashUID[(0040,A172)]    // Referenced Observation UID (Trial)
(0040,A402) := hashUID[(0040,A402)]    // Observation Subject UID (Trial)
(0040,DB0C) := hashUID[(0040,DB0C)]    // Template Extension Organization UID
(0040,DB0D) := hashUID[(0040,DB0D)]    // Template Extension Creator UID
(0062,0021) := hashUID[(0062,0021)]    // Tracking UID
(0064,0003) := hashUID[(0064,0003)]    // Source Frame of Reference UID
(0070,031A) := hashUID[(0070,031A)]    // Fiducial UID
(0070,1101) := hashUID[(0070,1101)]    // Presentation Display Collection UID
(0070,1102) := hashUID[(0070,1102)]    // Presentation Sequence Collection UID
(0088,0140) := hashUID[(0088,0140)]    // Storage Media File-set UID
(0400,0100) := hashUID[(0400,0100)]    // Digital Signature UID
(3006,0024) := hashUID[(3006,0024)]    // Referenced Frame of Reference UID
(3006,00C2) := hashUID[(3006,00C2)]    // Related Frame of Reference UID
(300A,0013) := hashUID[(300A,0013)]    // Dose Reference UID
(300A,0054) := hashUID[(300A,0054)]    // Table Top Position Alignment UID
(300A,0083) := hashUID[(300A,0083)]    // Referenced Dose Reference UID
(300A,0609) := hashUID[(300A,0609)]    // Treatment Position Group UID
(300A,0650) := hashUID[(300A,0650)]    // Patient Setup UID
(300A,0700) := hashUID[(300A,0700)]    // Treatment Session UID
(300A,0785) := hashUID[(300A,0785)]    // Referenced Treatment Position Group UID
(3010,0006) := hashUID[(3010,0006)]    // Conceptual Volume UID
(3010,000B) := hashUID[(3010,000B)]    // Referenced Conceptual Volume UID
(3010,0013) := hashUID[(3010,0013)]    // Constituent Conceptual Volume UID
(3010,0015) := hashUID[(3010,0015)]    // Source Conceptual Volume UID
(3010,0031) := hashUID[(3010,0031)]    // Referenced Fiducials UID
(3010,003B) := hashUID[(3010,003B)]    // RT Treatment Phase UID
(3010,006E) := hashUID[(3010,006E)]    // Dosimetric Objective UID
(3010,006F) := hashUID[(3010,006F)]    // Referenced Dosimetric Objective UID

// Remove the nested copies of those UIDs
- +/(0004,1511)    // Referenced SOP Instance UID in File
- +/(0008,0014)    // Instance Creator UID
- +/(0008,0017)    // Acquisition UID
- +/(0008,0018)    // SOP Instance UID
- +/(0008,0019)    // Pyramid UID
- +/(0008,1155)    // Referenced SOP Instance UID
- +/(0008,1195)    // Transaction UID
- +/(0018,1002)    // Device UID
- +/(0018,2042)    // Target UID
- +/(0020,000D)    // Study Instance UID
- +/(0020,000E)    // Series Instance UID
- +/(0020,0052)    // Frame of Reference UID
- +/(0020,0200)    // Synchronization Frame of Reference UID
- +/(0020,9161)    // Concatenation UID
- +/(0020,9164)    // Dimension Organization UID
- +/(0028,1199)    // Palette Color Lookup Table UID
- +/(0028,1214)    // Large Palette Color Lookup Table UID
- +/(003A,0310)    // Multiplex Group UID
- +/(0040,0554)    // Specimen UID
- +/(0040,4023)    // Referenced General Purpose Scheduled Procedure Step Transaction UID
- +/(0040,A124)    // UID
- +/(0040,A171)    // Observation UID
- +/(0040,A172)    // Referenced Observation UID (Trial)
- +/(0040,A402)    // Observation Subject UID (Trial)
- +/(0040,DB0C)    // Template Extension Organization UID
- +/(0040,DB0D)    // Template Extension Creator UID
- +/(0062,0021)    // Tracking UID
- +/(0064,0003)    // Source Frame of Reference UID
- +/(0070,031A)    // Fiducial UID
- +/(0070,1101)    // Presentation Display Collection UID
- +/(0070,1102)    // Presentation Sequence Collection UID
- +/(0088,0140)    // Storage Media File-set UID
- +/(0400,0100)    // Digital Signature UID
- +/(3006,0024)    // Referenced Frame of Reference UID
- +/(3006,00C2)    // Related Frame of Reference UID
- +/(300A,0013)    // Dose Reference UID
- +/(300A,0054)    // Table Top Position Alignment UID
- +/(300A,0083)    // Referenced Dose Reference UID
- +/(300A,0609)    // Treatment Position Group UID
- +/(300A,0650)    // Patient Setup UID
- +/(300A,0700)    // Treatment Session UID
- +/(300A,0785)    // Referenced Treatment Position Group UID
- +/(3010,0006)    // Conceptual Volume UID
- +/(3010,000B)    // Referenced Conceptual Volume UID
- +/(3010,0013)    // Constituent Conceptual Volume UID
- +/(3010,0015)    // Source Conceptual Volume UID
- +/(3010,0031)    // Referenced Fiducials UID
- +/(3010,003B)    // RT Treatment Phase UID
- +/(3010,006E)    // Dosimetric Objective UID
- +/(3010,006F)    // Referenced Dosimetric Objective UID

// Remove every other attribute the PS3.15 Basic Profile acts on (Table E.1-1), at any depth.
// The table's empty (Z) and dummy (D) actions are applied as removal too.
- */(0000,1000)    // Affected SOP Instance UID
- */(0008,0012)    // Instance Creation Date
- */(0008,0013)    // Instance Creation Time
- */(0008,0015)    // Instance Coercion DateTime
- */(0008,0020)    // Study Date
- */(0008,0021)    // Series Date
- */(0008,0022)    // Acquisition Date
- */(0008,0023)    // Content Date
- */(0008,0024)    // Overlay Date
- */(0008,0025)    // Curve Date
- */(0008,002A)    // Acquisition DateTime
- */(0008,0030)    // Study Time
- */(0008,0031)    // Series Time
- */(0008,0032)    // Acquisition Time
- */(0008,0033)    // Content Time
- */(0008,0034)    // Overlay Time
- */(0008,0035)    // Curve Time
- */(0008,0050)    // Accession Number
- */(0008,0054)    // Retrieve AE Title
- */(0008,0055)    // Station AE Title
- */(0008,0058)    // Failed SOP Instance UID List
- */(0008,0080)    // Institution Name
- */(0008,0081)    // Institution Address
- */(0008,0082)    // Institution Code Sequence
- */(0008,0090)    // Referring Physician's Name
- */(0008,0092)    // Referring Physician's Address
- */(0008,0094)    // Referring Physician's Telephone Numbers
- */(0008,0096)    // Referring Physician Identification Sequence
- */(0008,009C)    // Consulting Physician's Name
- */(0008,009D)    // Consulting Physician Identification Sequence
- */(0008,0106)    // Context Group Version
- */(0008,0107)    // Context Group Local Version
- */(0008,0201)    // Timezone Offset From UTC
- */(0008,1000)    // Network ID
- */(0008,1010)    // Station Name
- */(0008,1030)    // Study Description
- */(0008,103E)    // Series Description
- */(0008,1040)    // Institutional Department Name
- */(0008,1041)    // Institutional Department Type Code Sequence
- */(0008,1048)    // Physician(s) of Record
- */(0008,1049)    // Physician(s) of Record Identification Sequence
- */(0008,1050)    // Performing Physician's Name
- */(0008,1052)    // Performing Physician Identification Sequence
- */(0008,1060)    // Name of Physician(s) Reading Study
- */(0008,1062)    // Physician(s) Reading Study Identification Sequence
- */(0008,1070)    // Operators' Name
- */(0008,1072)    // Operator Identification Sequence
- */(0008,1080)    // Admitting Diagnoses Description
- */(0008,1084)    // Admitting Diagnoses Code Sequence
- */(0008,1088)    // Pyramid Description
- */(0008,1110)    // Referenced Study Sequence
- */(0008,1111)    // Referenced Performed Procedure Step Sequence
- */(0008,1120)    // Referenced Patient Sequence
- */(0008,1140)    // Referenced Image Sequence
- */(0008,1301)    // Principal Diagnosis Code Sequence
- */(0008,1302)    // Primary Diagnosis Code Sequence
- */(0008,1303)    // Secondary Diagnoses Code Sequence
- */(0008,1304)    // Histological Diagnoses Code Sequence
- */(0008,2111)    // Derivation Description
- */(0008,2112)    // Source Image Sequence
- */(0008,3010)    // Irradiation Event UID
- */(0008,4000)    // Identifying Comments
- */(0010,0011)    // Person Names to Use Sequence
- */(0010,0012)    // Name to Use
- */(0010,0013)    // Name to Use Comment
- */(0010,0014)    // Third Person Pronouns Sequence
- */(0010,0015)    // Pronoun Code Sequence
- */(0010,0016)    // Pronoun Comment
- */(0010,0021)    // Issuer of Patient ID
- */(0010,0030)    // Patient's Birth Date
- */(0010,0032)    // Patient's Birth Time
- */(0010,0040)    // Patient's Sex
- */(0010,0041)    // Gender Identity Sequence
- */(0010,0042)    // Sex Parameters for Clinical Use Category Comment
- */(0010,0043)    // Sex Parameters for Clinical Use Category Sequence
- */(0010,0044)    // Gender Identity Code Sequence
- */(0010,0045)    // Gender Identity Comment
- */(0010,0046)    // Sex Parameters for Clinical Use Category Code Sequence
- */(0010,0047)    // Sex Parameters for Clinical Use Category Reference
- */(0010,0050)    // Patient's Insurance Plan Code Sequence
- */(0010,0101)    // Patient's Primary Language Code Sequence
- */(0010,0102)    // Patient's Primary Language Modifier Code Sequence
- */(0010,1000)    // Other Patient IDs
- */(0010,1001)    // Other Patient Names
- */(0010,1002)    // Other Patient IDs Sequence
- */(0010,1005)    // Patient's Birth Name
- */(0010,1010)    // Patient's Age
- */(0010,1020)    // Patient's Size
- */(0010,1030)    // Patient's Weight
- */(0010,1040)    // Patient's Address
- */(0010,1050)    // Insurance Plan Identification
- */(0010,1060)    // Patient's Mother's Birth Name
- */(0010,1080)    // Military Rank
- */(0010,1081)    // Branch of Service
- */(0010,1090)    // Medical Record Locator
- */(0010,1100)    // Referenced Patient Photo Sequence
- */(0010,2000)    // Medical Alerts
- */(0010,2110)    // Allergies
- */(0010,2150)    // Country of Residence
- */(0010,2152)    // Region of Residence
- */(0010,2154)    // Patient's Telephone Numbers
- */(0010,2155)    // Patient's Telecom Information
- */(0010,2160)    // Ethnic Group
- */(0010,2161)    // Ethnic Group Code Sequence
- */(0010,2162)    // Ethnic Groups
- */(0010,2180)    // Occupation
- */(0010,21A0)    // Smoking Status
- */(0010,21B0)    // Additional Patient History
- */(0010,21C0)    // Pregnancy Status
- */(0010,21D0)    // Last Menstrual Date
- */(0010,21F0)    // Patient's Religious Preference
- */(0010,2203)    // Patient's Sex Neutered
- */(0010,2297)    // Responsible Person
- */(0010,2299)    // Responsible Organization
- */(0010,4000)    // Patient Comments (drops the project label written above too)
- */(0012,0010)    // Clinical Trial Sponsor Name
- */(0012,0020)    // Clinical Trial Protocol ID
- */(0012,0021)    // Clinical Trial Protocol Name
- */(0012,0022)    // Issuer of Clinical Trial Protocol ID
- */(0012,0023)    // Other Clinical Trial Protocol IDs Sequence
- */(0012,0030)    // Clinical Trial Site ID
- */(0012,0031)    // Clinical Trial Site Name
- */(0012,0032)    // Issuer of Clinical Trial Site ID
- */(0012,0040)    // Clinical Trial Subject ID
- */(0012,0041)    // Issuer of Clinical Trial Subject ID
- */(0012,0042)    // Clinical Trial Subject Reading ID
- */(0012,0043)    // Issuer of Clinical Trial Subject Reading ID
- */(0012,0050)    // Clinical Trial Time Point ID
- */(0012,0051)    // Clinical Trial Time Point Description
- */(0012,0055)    // Issuer of Clinical Trial Time Point ID
- */(0012,0060)    // Clinical Trial Coordinating Center Name
- */(0012,0071)    // Clinical Trial Series ID
- */(0012,0072)    // Clinical Trial Series Description
- */(0012,0073)    // Issuer of Clinical Trial Series ID
- */(0012,0081)    // Clinical Trial Protocol Ethics Committee Name
- */(0012,0082)    // Clinical Trial Protocol Ethics Committee Approval Number
- */(0012,0086)    // Ethics Committee Approval Effectiveness Start Date
- */(0012,0087)    // Ethics Committee Approval Effectiveness End Date
- */(0014,407C)    // Calibration Time
- */(0014,407E)    // Calibration Date
- */(0016,002B)    // Maker Note
- */(0016,004B)    // Device Setting Description
- */(0016,004D)    // Camera Owner Name
- */(0016,004E)    // Lens Specification
- */(0016,004F)    // Lens Make
- */(0016,0050)    // Lens Model
- */(0016,0051)    // Lens Serial Number
- */(0016,0070)    // GPS Version ID
- */(0016,0071)    // GPS Latitude​ Ref
- */(0016,0072)    // GPS Latitude​
- */(0016,0073)    // GPS Longitude Ref
- */(0016,0074)    // GPS Longitude
- */(0016,0075)    // GPS Altitude​ Ref
- */(0016,0076)    // GPS Altitude​
- */(0016,0077)    // GPS Time​ Stamp
- */(0016,0078)    // GPS Satellites
- */(0016,0079)    // GPS Status
- */(0016,007A)    // GPS Measure ​Mode
- */(0016,007B)    // GPS DOP
- */(0016,007C)    // GPS Speed​ Ref
- */(0016,007D)    // GPS Speed​
- */(0016,007E)    // GPS Track ​Ref
- */(0016,007F)    // GPS Track
- */(0016,0080)    // GPS Img​ Direction Ref
- */(0016,0081)    // GPS Img ​Direction
- */(0016,0082)    // GPS Map​ Datum
- */(0016,0083)    // GPS Dest​ Latitude Ref
- */(0016,0084)    // GPS Dest​ Latitude
- */(0016,0085)    // GPS Dest ​Longitude Ref
- */(0016,0086)    // GPS Dest ​Longitude
- */(0016,0087)    // GPS Dest​ Bearing Ref
- */(0016,0088)    // GPS Dest ​Bearing
- */(0016,0089)    // GPS Dest ​Distance Ref
- */(0016,008A)    // GPS Dest ​Distance
- */(0016,008B)    // GPS Processing​ Method
- */(0016,008C)    // GPS Area ​Information
- */(0016,008D)    // GPS Date​ Stamp
- */(0016,008E)    // GPS Differential
- */(0018,0010)    // Contrast/Bolus Agent
- */(0018,0027)    // Intervention Drug Stop Time
- */(0018,0035)    // Intervention Drug Start Time
- */(0018,1000)    // Device Serial Number
- */(0018,1004)    // Plate ID
- */(0018,1005)    // Generator ID
- */(0018,1007)    // Cassette ID
- */(0018,1008)    // Gantry ID
- */(0018,1009)    // Unique Device Identifier
- */(0018,100A)    // UDI Sequence
- */(0018,100B)    // Manufacturer's Device Class UID
- */(0018,1010)    // Secondary Capture Device ID
- */(0018,1011)    // Hardcopy Creation Device ID
- */(0018,1012)    // Date of Secondary Capture
- */(0018,1014)    // Time of Secondary Capture
- */(0018,1030)    // Protocol Name
- */(0018,1042)    // Contrast/Bolus Start Time
- */(0018,1043)    // Contrast/Bolus Stop Time
- */(0018,1072)    // Radiopharmaceutical Start Time
- */(0018,1073)    // Radiopharmaceutical Stop Time
- */(0018,1078)    // Radiopharmaceutical Start DateTime
- */(0018,1079)    // Radiopharmaceutical Stop DateTime
- */(0018,11BB)    // Acquisition Field of View Label
- */(0018,1200)    // Date of Last Calibration
- */(0018,1201)    // Time of Last Calibration
- */(0018,1202)    // DateTime of Last Calibration
- */(0018,1203)    // Calibration DateTime
- */(0018,1204)    // Date of Manufacture
- */(0018,1205)    // Date of Installation
- */(0018,1400)    // Acquisition Device Processing Description
- */(0018,4000)    // Acquisition Comments
- */(0018,5011)    // Transducer Identification Sequence
- */(0018,700A)    // Detector ID
- */(0018,700C)    // Date of Last Detector Calibration
- */(0018,700E)    // Time of Last Detector Calibration
- */(0018,9074)    // Frame Acquisition DateTime
- */(0018,9151)    // Frame Reference DateTime
- */(0018,9185)    // Respiratory Motion Compensation Technique Description
- */(0018,9367)    // X-Ray Source ID
- */(0018,9369)    // Source Start DateTime
- */(0018,936A)    // Source End DateTime
- */(0018,9371)    // X-Ray Detector ID
- */(0018,9373)    // X-Ray Detector Label
- */(0018,937B)    // Multi-energy Acquisition Description
- */(0018,937F)    // Decomposition Description
- */(0018,9424)    // Acquisition Protocol Description
- */(0018,9516)    // Start Acquisition DateTime
- */(0018,9517)    // End Acquisition DateTime
- */(0018,9623)    // Functional Sync Pulse
- */(0018,9701)    // Decay Correction DateTime
- */(0018,9804)    // Exclusion Start DateTime
- */(0018,9919)    // Instruction Performed DateTime
- */(0018,9937)    // Requested Series Description
- */(0018,A002)    // Contribution DateTime
- */(0018,A003)    // Contribution Description
- */(0020,0010)    // Study ID
- */(0020,0027)    // Pyramid Label
- */(0020,3401)    // Modifying Device ID
- */(0020,3403)    // Modified Image Date
- */(0020,3405)    // Modified Image Time
- */(0020,3406)    // Modified Image Description
- */(0020,4000)    // Image Comments
- */(0020,9158)    // Frame Comments
- */(0028,4000)    // Image Presentation Comments
- */(0032,0012)    // Study ID Issuer
- */(0032,0032)    // Study Verified Date
- */(0032,0033)    // Study Verified Time
- */(0032,0034)    // Study Read Date
- */(0032,0035)    // Study Read Time
- */(0032,1000)    // Scheduled Study Start Date
- */(0032,1001)    // Scheduled Study Start Time
- */(0032,1010)    // Scheduled Study Stop Date
- */(0032,1011)    // Scheduled Study Stop Time
- */(0032,1020)    // Scheduled Study Location
- */(0032,1021)    // Scheduled Study Location AE Title
- */(0032,1030)    // Reason for Study
- */(0032,1032)    // Requesting Physician
- */(0032,1033)    // Requesting Service
- */(0032,1040)    // Study Arrival Date
- */(0032,1041)    // Study Arrival Time
- */(0032,1050)    // Study Completion Date
- */(0032,1051)    // Study Completion Time
- */(0032,1060)    // Requested Procedure Description
- */(0032,1066)    // Reason for Visit
- */(0032,1067)    // Reason for Visit Code Sequence
- */(0032,1070)    // Requested Contrast Agent
- */(0032,4000)    // Study Comments
- */(0034,0001)    // Flow Identifier Sequence
- */(0034,0002)    // Flow Identifier
- */(0034,0005)    // Source Identifier
- */(0034,0007)    // Frame Origin Timestamp
- */(0038,0004)    // Referenced Patient Alias Sequence
- */(0038,0010)    // Admission ID
- */(0038,0011)    // Issuer of Admission ID
- */(0038,0014)    // Issuer of Admission ID Sequence
- */(0038,001A)    // Scheduled Admission Date
- */(0038,001B)    // Scheduled Admission Time
- */(0038,001C)    // Scheduled Discharge Date
- */(0038,001D)    // Scheduled Discharge Time
- */(0038,001E)    // Scheduled Patient Institution Residence
- */(0038,0020)    // Admitting Date
- */(0038,0021)    // Admitting Time
- */(0038,0030)    // Discharge Date
- */(0038,0032)    // Discharge Time
- */(0038,0040)    // Discharge Diagnosis Description
- */(0038,0050)    // Special Needs
- */(0038,0060)    // Service Episode ID
- */(0038,0061)    // Issuer of Service Episode ID
- */(0038,0062)    // Service Episode Description
- */(0038,0064)    // Issuer of Service Episode ID Sequence
- */(0038,0300)    // Current Patient Location
- */(0038,0400)    // Patient's Institution Residence
- */(0038,0500)    // Patient State
- */(0038,4000)    // Visit Comments
- */(003A,0020)    // Multiplex Group Label
- */(003A,0203)    // Channel Label
- */(003A,020C)    // Channel Derivation Description
- */(003A,0314)    // Impedance Measurement DateTime
- */(003A,0329)    // ​Waveform Filter Description
- */(003A,032B)    // Filter Lookup Table Description
- */(0040,0001)    // Scheduled Station AE Title
- */(0040,0002)    // Scheduled Procedure Step Start Date
- */(0040,0003)    // Scheduled Procedure Step Start Time
- */(0040,0004)    // Scheduled Procedure Step End Date
- */(0040,0005)    // Scheduled Procedure Step End Time
- */(0040,0006)    // Scheduled Performing Physician's Name
- */(0040,0007)    // Scheduled Procedure Step Description
- */(0040,0009)    // Scheduled Procedure Step ID
- */(0040,000B)    // Scheduled Performing Physician Identification Sequence
- */(0040,0010)    // Scheduled Station Name
- */(0040,0011)    // Scheduled Procedure Step Location
- */(0040,0012)    // Pre-Medication
- */(0040,0241)    // Performed Station AE Title
- */(0040,0242)    // Performed Station Name
- */(0040,0243)    // Performed Location
- */(0040,0244)    // Performed Procedure Step Start Date
- */(0040,0245)    // Performed Procedure Step Start Time
- */(0040,0250)    // Performed Procedure Step End Date
- */(0040,0251)    // Performed Procedure Step End Time
- */(0040,0253)    // Performed Procedure Step ID
- */(0040,0254)    // Performed Procedure Step Description
- */(0040,0275)    // Request Attributes Sequence
- */(0040,0280)    // Comments on the Performed Procedure Step
- */(0040,0310)    // Comments on Radiation Dose
- */(0040,050A)    // Specimen Accession Number
- */(0040,0512)    // Container Identifier
- */(0040,0513)    // Issuer of the Container Identifier Sequence
- */(0040,051A)    // Container Description
- */(0040,0551)    // Specimen Identifier
- */(0040,0555)    // Acquisition Context Sequence
- */(0040,0556)    // Acquisition Context Description
- */(0040,0562)    // Issuer of the Specimen Identifier Sequence
- */(0040,0600)    // Specimen Short Description
- */(0040,0602)    // Specimen Detailed Description
- */(0040,0610)    // Specimen Preparation Sequence
- */(0040,06FA)    // Slide Identifier
- */(0040,1001)    // Requested Procedure ID
- */(0040,1002)    // Reason for the Requested Procedure
- */(0040,1004)    // Patient Transport Arrangements
- */(0040,1005)    // Requested Procedure Location
- */(0040,100A)    // Reason for Requested Procedure Code Sequence
- */(0040,1010)    // Names of Intended Recipients of Results
- */(0040,1011)    // Intended Recipients of Results Identification Sequence
- */(0040,1101)    // Person Identification Code Sequence
- */(0040,1102)    // Person's Address
- */(0040,1103)    // Person's Telephone Numbers
- */(0040,1104)    // Person's Telecom Information
- */(0040,1400)    // Requested Procedure Comments
- */(0040,2001)    // Reason for the Imaging Service Request
- */(0040,2004)    // Issue Date of Imaging Service Request
- */(0040,2005)    // Issue Time of Imaging Service Request
- */(0040,2008)    // Order Entered By
- */(0040,2009)    // Order Enterer's Location
- */(0040,2010)    // Order Callback Phone Number
- */(0040,2011)    // Order Callback Telecom Information
- */(0040,2016)    // Placer Order Number / Imaging Service Request
- */(0040,2017)    // Filler Order Number / Imaging Service Request
- */(0040,2400)    // Imaging Service Request Comments
- */(0040,3001)    // Confidentiality Constraint on Patient Data Description
- */(0040,4005)    // Scheduled Procedure Step Start DateTime
- */(0040,4008)    // Scheduled Procedure Step Expiration DateTime
- */(0040,4010)    // Scheduled Procedure Step Modification DateTime
- */(0040,4011)    // Expected Completion DateTime
- */(0040,4025)    // Scheduled Station Name Code Sequence
- */(0040,4027)    // Scheduled Station Geographic Location Code Sequence
- */(0040,4028)    // Performed Station Name Code Sequence
- */(0040,4030)    // Performed Station Geographic Location Code Sequence
- */(0040,4034)    // Scheduled Human Performers Sequence
- */(0040,4035)    // Actual Human Performers Sequence
- */(0040,4036)    // Human Performer's Organization
- */(0040,4037)    // Human Performer's Name
- */(0040,4050)    // Performed Procedure Step Start DateTime
- */(0040,4051)    // Performed Procedure Step End DateTime
- */(0040,4052)    // Procedure Step Cancellation DateTime
- */(0040,A023)    // Findings Group Recording Date (Trial)
- */(0040,A024)    // Findings Group Recording Time (Trial)
- */(0040,A027)    // Verifying Organization
- */(0040,A030)    // Verification DateTime
- */(0040,A032)    // Observation DateTime
- */(0040,A033)    // Observation Start DateTime
- */(0040,A034)    // Effective Start DateTime
- */(0040,A035)    // Effective Stop DateTime
- */(0040,A073)    // Verifying Observer Sequence
- */(0040,A075)    // Verifying Observer Name
- */(0040,A078)    // Author Observer Sequence
- */(0040,A07A)    // Participant Sequence
- */(0040,A07C)    // Custodial Organization Sequence
- */(0040,A082)    // Participation DateTime
- */(0040,A088)    // Verifying Observer Identification Code Sequence
- */(0040,A110)    // Date of Document or Verbal Transaction (Trial)
- */(0040,A112)    // Time of Document Creation or Verbal Transaction (Trial)
- */(0040,A120)    // DateTime
- */(0040,A121)    // Date
- */(0040,A122)    // Time
- */(0040,A123)    // Person Name
- */(0040,A13A)    // Referenced DateTime
- */(0040,A192)    // Observation Date (Trial)
- */(0040,A193)    // Observation Time (Trial)
- */(0040,A307)    // Current Observer (Trial)
- */(0040,A352)    // Verbal Source (Trial)
- */(0040,A353)    // Address (Trial)
- */(0040,A354)    // Telephone Number (Trial)
- */(0040,A358)    // Verbal Source Identifier Code Sequence (Trial)
- */(0040,A730)    // Content Sequence
- */(0040,B020)    // Waveform Annotation Sequence
- */(0040,B034)    // Annotation DateTime
- */(0040,B036)    // Segment Definition DateTime
- */(0040,B03B)    // Montage Name
- */(0040,B03F)    // Montage Channel Label
- */(0040,DB06)    // Template Version
- */(0040,DB07)    // Template Local Version
- */(0040,E004)    // HL7 Document Effective Time
- */(0040,E012)    // Display URI
- */(0042,0011)    // Encapsulated Document
- */(0044,0004)    // Approval Status DateTime
- */(0044,000B)    // Product Expiration DateTime
- */(0044,0010)    // Substance Administration DateTime
- */(0044,0104)    // Assertion DateTime
- */(0044,0105)    // Assertion Expiration DateTime
- */(0050,001B)    // Container Component ID
- */(0050,0020)    // Device Description
- */(0050,0021)    // Long Device Description
- */(0068,6226)    // Effective DateTime
- */(0068,6270)    // Information Issue DateTime
- */(006A,0003)    // Annotation Group UID
- */(006A,0005)    // Annotation Group Label
- */(006A,0006)    // Annotation Group Description
- */(0070,0001)    // Graphic Annotation Sequence
- */(0070,0006)    // Unformatted Text Value
- */(0070,0082)    // Presentation Creation Date
- */(0070,0083)    // Presentation Creation Time
- */(0070,0084)    // Content Creator's Name
- */(0070,0086)    // Content Creator's Identification Code Sequence
- */(0072,000A)    // Hanging Protocol Creation DateTime
- */(0072,005E)    // Selector AE Value
- */(0072,005F)    // Selector AS Value
- */(0072,0061)    // Selector DA Value
- */(0072,0063)    // Selector DT Value
- */(0072,0065)    // Selector OB Value
- */(0072,0066)    // Selector LO Value
- */(0072,0068)    // Selector LT Value
- */(0072,006A)    // Selector PN Value
- */(0072,006B)    // Selector TM Value
- */(0072,006C)    // Selector SH Value
- */(0072,006D)    // Selector UN Value
- */(0072,006E)    // Selector ST Value
- */(0072,0070)    // Selector UT Value
- */(0072,0071)    // Selector UR Value
- */(0074,1234)    // Receiving AE
- */(0074,1236)    // Requesting AE
- */(0088,0200)    // Icon Image Sequence (see Note 11)
- */(0088,0904)    // Topic Title
- */(0088,0906)    // Topic Subject
- */(0088,0910)    // Topic Author
- */(0088,0912)    // Topic Keywords
- */(0100,0420)    // SOP Authorization DateTime
- */(0400,0105)    // Digital Signature DateTime
- */(0400,0115)    // Certificate of Signer
- */(0400,0310)    // Certified Timestamp
- */(0400,0402)    // Referenced Digital Signature Sequence
- */(0400,0403)    // Referenced SOP Instance MAC Sequence
- */(0400,0404)    // MAC
- */(0400,0550)    // Modified Attributes Sequence
- */(0400,0551)    // Nonconforming Modified Attributes Sequence
- */(0400,0552)    // Nonconforming Data Element Value
- */(0400,0561)    // Original Attributes Sequence
- */(0400,0562)    // Attribute Modification DateTime
- */(0400,0563)    // Modifying System
- */(0400,0564)    // Source of Previous Values
- */(0400,0565)    // Reason for the Attribute Modification
- */(0400,0600)    // Instance Origin Status
- */(2030,0020)    // Text String
- */(2100,0040)    // Creation Date
- */(2100,0050)    // Creation Time
- */(2100,0070)    // Originator
- */(2100,0140)    // Destination AE
- */(2200,0002)    // Label Text
- */(2200,0005)    // Barcode Value
- */(3002,0121)    // Position Acquisition Template Name
- */(3002,0123)    // Position Acquisition Template Description
- */(3004,007F)    // Dose Calculation Model Name
- */(3006,0002)    // Structure Set Label
- */(3006,0004)    // Structure Set Name
- */(3006,0006)    // Structure Set Description
- */(3006,0008)    // Structure Set Date
- */(3006,0009)    // Structure Set Time
- */(3006,0026)    // ROI Name
- */(3006,0028)    // ROI Description
- */(3006,002D)    // ROI DateTime
- */(3006,002E)    // ROI Observation DateTime
- */(3006,0038)    // ROI Generation Description
- */(3006,004D)    // ROI Creator Sequence
- */(3006,004E)    // ROI Interpreter Sequence
- */(3006,0085)    // ROI Observation Label
- */(3006,0088)    // ROI Observation Description
- */(3006,00A6)    // ROI Interpreter
- */(3008,0024)    // Treatment Control Point Date
- */(3008,0025)    // Treatment Control Point Time
- */(3008,0054)    // First Treatment Date
- */(3008,0056)    // Most Recent Treatment Date
- */(3008,0105)    // Source Serial Number
- */(3008,0162)    // Safe Position Exit Date
- */(3008,0164)    // Safe Position Exit Time
- */(3008,0166)    // Safe Position Return Date
- */(3008,0168)    // Safe Position Return Time
- */(3008,0250)    // Treatment Date
- */(3008,0251)    // Treatment Time
- */(300A,0002)    // RT Plan Label
- */(300A,0003)    // RT Plan Name
- */(300A,0004)    // RT Plan Description
- */(300A,0006)    // RT Plan Date
- */(300A,0007)    // RT Plan Time
- */(300A,000B)    // Treatment Sites
- */(300A,000E)    // Prescription Description
- */(300A,0016)    // Dose Reference Description
- */(300A,0072)    // Fraction Group Description
- */(300A,00B2)    // Treatment Machine Name
- */(300A,00C3)    // Beam Description
- */(300A,00DD)    // Bolus Description
- */(300A,0196)    // Fixation Device Description
- */(300A,01A6)    // Shielding Device Description
- */(300A,01B2)    // Setup Technique Description
- */(300A,0216)    // Source Manufacturer
- */(300A,022C)    // Source Strength Reference Date
- */(300A,022E)    // Source Strength Reference Time
- */(300A,02EB)    // Compensator Description
- */(300A,0608)    // Treatment Position Group Label
- */(300A,0611)    // RT Accessory Holder Slot ID
- */(300A,0615)    // RT Accessory Device Slot ID
- */(300A,0619)    // Radiation Dose Identification Label
- */(300A,0623)    // Radiation Dose In-Vivo Measurement Label
- */(300A,062A)    // RT Tolerance Set Label
- */(300A,0676)    // Equipment Frame of Reference Description
- */(300A,067C)    // Radiation Generation Mode Label
- */(300A,067D)    // Radiation Generation Mode Description
- */(300A,0734)    // Treatment Tolerance Violation Description
- */(300A,0736)    // Treatment Tolerance Violation DateTime
- */(300A,073A)    // Recorded RT Control Point DateTime
- */(300A,0741)    // Interlock DateTime
- */(300A,0742)    // Interlock Description
- */(300A,0760)    // Override DateTime
- */(300A,0783)    // Interlock Origin Description
- */(300A,078E)    // Patient Treatment Preparation Procedure Parameter Description
- */(300A,0792)    // Patient Treatment Preparation Method Description
- */(300A,0794)    // Patient Setup Photo Description
- */(300A,079A)    // Displacement Reference Label
- */(300C,0113)    // Reason for Omission Description
- */(300C,0127)    // Beam Hold Transition DateTime
- */(300E,0004)    // Review Date
- */(300E,0005)    // Review Time
- */(300E,0008)    // Reviewer Name
- */(3010,000F)    // Conceptual Volume Combination Description
- */(3010,0017)    // Conceptual Volume Description
- */(3010,001B)    // Device Alternate Identifier
- */(3010,002D)    // Device Label
- */(3010,0033)    // User Content Label
- */(3010,0034)    // User Content Long Label
- */(3010,0035)    // Entity Label
- */(3010,0036)    // Entity Name
- */(3010,0037)    // Entity Description
- */(3010,0038)    // Entity Long Label
- */(3010,0043)    // Manufacturer's Device Identifier
- */(3010,004C)    // Intended Phase Start Date
- */(3010,004D)    // Intended Phase End Date
- */(3010,0054)    // RT Prescription Label
- */(3010,0056)    // RT Treatment Approach Label
- */(3010,005A)    // RT Physician Intent Narrative
- */(3010,005C)    // Reason for Superseding
- */(3010,0061)    // Prior Treatment Dose Description
- */(3010,0077)    // Treatment Site
- */(3010,007A)    // Treatment Technique Notes
- */(3010,007B)    // Prescription Notes
- */(3010,007F)    // Fractionation Notes
- */(3010,0081)    // Prescription Notes Sequence
- */(3010,0085)    // Intended Fraction Start Time
- */(4000,0010)    // Arbitrary
- */(4000,4000)    // Text Comments
- */(4008,0040)    // Results ID
- */(4008,0042)    // Results ID Issuer
- */(4008,0100)    // Interpretation Recorded Date
- */(4008,0101)    // Interpretation Recorded Time
- */(4008,0102)    // Interpretation Recorder
- */(4008,0108)    // Interpretation Transcription Date
- */(4008,0109)    // Interpretation Transcription Time
- */(4008,010A)    // Interpretation Transcriber
- */(4008,010B)    // Interpretation Text
- */(4008,010C)    // Interpretation Author
- */(4008,0111)    // Interpretation Approver Sequence
- */(4008,0112)    // Interpretation Approval Date
- */(4008,0113)    // Interpretation Approval Time
- */(4008,0114)    // Physician Approving Interpretation
- */(4008,0115)    // Interpretation Diagnosis Description
- */(4008,0118)    // Results Distribution List Sequence
- */(4008,0119)    // Distribution Name
- */(4008,011A)    // Distribution Address
- */(4008,0200)    // Interpretation ID
- */(4008,0202)    // Interpretation ID Issuer
- */(4008,0300)    // Impressions
- */(4008,4000)    // Results Comments
- */(50X@,XXXX)    // Curve Data
- */(60X@,3000)    // Overlay Data
- */(60X@,4000)    // Overlay Comments
- */(FFFA,FFFA)    // Digital Signatures Sequence
- */(FFFC,FFFC)    // Data Set Trailing Padding

// Record de-identification. The method says what falls short of PS3.15; one value
// per statement, each at most 64 characters (VR LO, VM 1-n).
(0012,0062) := "YES"                                    // Patient Identity Removed
(0012,0063) := "FLIP site script v2, not a conformant PS3.15 profile\\Removed: PS3.15 Basic Profile attributes and private tags\\Removed, not hashed: UIDs inside sequences\\Not checked: identifiers burned into pixels"
