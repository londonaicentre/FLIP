version "6.3"
// FLIP Site-Wide DICOM Anonymization Script
// Compatible with XNAT 1.9.3 (DicomEdit 6.7.x)
//
// This script is applied to all incoming DICOM data when anonymizationEnabled
// is set to true on the SCP receiver. It removes Protected Health Information
// (PHI) from DICOM headers to ensure patient privacy.

// Store XNAT labels into DICOM headers
(0010,4000) := project
(0010,0010) := subject
(0008,1030) := session

// Remove direct patient identifiers
- (0010,0030)    // Patient Birth Date
- (0010,1000)    // Other Patient IDs
- (0010,1001)    // Other Patient Names
- (0010,1005)    // Patient Birth Name
- (0010,1040)    // Patient Address
- (0010,2154)    // Patient Telephone Numbers
- (0010,0032)    // Patient Birth Time

// Remove institutional identifiers
- (0008,0080)    // Institution Name
- (0008,0081)    // Institution Address
- (0008,1040)    // Institutional Department Name

// Remove physician/operator identifiers
- (0008,0090)    // Referring Physician Name
- (0008,0092)    // Referring Physician Address
- (0008,0094)    // Referring Physician Telephone Numbers
- (0008,1048)    // Physician(s) of Record
- (0008,1050)    // Performing Physician Name
- (0008,1070)    // Operators Name

// Remove other potentially identifying tags
- (0008,0050)    // Accession Number (we use relabelMap for session)
- (0010,1090)    // Medical Record Locator
- (0010,2160)    // Ethnic Group
- (0010,2180)    // Occupation
- (0010,21B0)    // Additional Patient History
- (0010,4000)    // Patient Comments (after project label stored above)
- (0032,1032)    // Requesting Physician
- (0040,0006)    // Scheduled Performing Physician Name
- (0040,2016)    // Placer Order Number
- (0040,2017)    // Filler Order Number
- (0040,A123)    // Person Name (in structured reports)

// Hash UIDs for pseudonymization (repeatable hashing)
(0020,000D) := hashUID[(0020,000D)]    // Study Instance UID
(0020,000E) := hashUID[(0020,000E)]    // Series Instance UID

// Record de-identification method
(0012,0062) := "YES"                                    // Patient Identity Removed
(0012,0063) := "FLIP Site-Wide Anonymization v1"         // De-identification Method
