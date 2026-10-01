"""Independent "real-world style" ICD-10-CM test set (31 synthetic notes, written separately from the tuned splits).

Every gold code was checked against the FY2026 CDC order file (exists, billable) and assigned by the
ICD-10-CM Official Guidelines FY2026. `optional` codes are acceptable but not required (e.g. external
cause codes, Z79.4 with type 1 diabetes); they are neither credited as missing nor penalized as extra.

    python scripts/build_realworld_set.py   ->  data/evaluation/realworld.jsonl
"""
import json
from pathlib import Path

CASES = [
    # id, encounter, sex, age, note, gold, optional, guideline focus
    ("RW01", "outpatient", "M", 67, """FAMILY MEDICINE VISIT
CC: Diabetes and kidney follow-up.
HPI: 67 y/o male with type 2 diabetes and HTN. Recent labs show eGFR 38, stable over 6 months.
Medications: metformin 500 mg BID, amlodipine 10 mg daily.
ROS: Denies chest pain, dyspnea, edema.
Assessment:
1. Type 2 diabetes mellitus with diabetic chronic kidney disease, CKD stage 3b
2. Hypertension
Plan: Continue current medications. Renal dosing reviewed. Recheck BMP in 3 months.""",
     ["E11.22", "I12.9", "N18.32", "Z79.84"], [], "I.C.4.a / I.C.9.a.2 HTN+CKD presumed link, use additional N18"),
    ("RW02", "inpatient", "F", 74, """DISCHARGE SUMMARY
Admission diagnosis: Shortness of breath.
Hospital course: 74-year-old woman with long-standing hypertension and chronic kidney disease stage 4 admitted with
worsening dyspnea and 3+ leg edema. BNP 2,400. Echo EF 30%. Diuresed with IV furosemide.
Discharge diagnoses:
1. Acute on chronic systolic congestive heart failure
2. Hypertension
3. Chronic kidney disease stage 4
Disposition: Home with home health.""",
     ["I13.0", "I50.23", "N18.4"], [], "I.C.9.a.3 hypertensive heart and CKD combination"),
    ("RW03", "outpatient", "M", 45, """URGENT CARE NOTE
CC: Cough and fever for 3 days.
HPI: 45-year-old man with productive cough and fever to 101.8 F. No hemoptysis.
Exam: Scattered rhonchi right base. SpO2 96%.
Assessment: Cough and fever - rule out pneumonia. Chest x-ray ordered, results pending.
Plan: Supportive care, follow up with x-ray result.""",
     ["R05.9", "R50.9"], [], "IV.H outpatient: do not code 'rule out' diagnoses; code symptoms"),
    ("RW04", "inpatient", "M", 70, """DISCHARGE SUMMARY
70-year-old male admitted with fever, cough and hypoxia requiring 2 L oxygen.
Chest x-ray with right lower lobe opacity. Treated with ceftriaxone and azithromycin, improved.
Discharge diagnosis: Probable community-acquired pneumonia.
Discharged home on oral antibiotics.""",
     ["J18.9"], ["R09.02"], "II.H inpatient: code 'probable' diagnosis at discharge as if established"),
    ("RW05", "outpatient", "F", 52, """ORTHOPEDIC CONSULT
CC: Right knee pain for 2 years.
HPI: Gradual onset, worse with stairs. No history of trauma. Denies fever or swelling of other joints.
Imaging: Medial joint space narrowing right knee with osteophytes.
Assessment: Primary osteoarthritis of the right knee.
Plan: Physical therapy, NSAIDs as needed, consider injection at next visit.""",
     ["M17.11"], [], "laterality, primary OA unilateral"),
    ("RW06", "outpatient", "M", 60, """PULMONARY CLINIC
CC: Worsening shortness of breath and wheeze for 4 days.
HPI: 60 y/o man with COPD presents with increased sputum and dyspnea. He currently smokes 1 pack of cigarettes per day.
Assessment:
1. COPD with acute exacerbation
2. Nicotine dependence, cigarettes
Plan: Prednisone burst, increase albuterol, smoking cessation counseling provided.""",
     ["J44.1", "F17.210"], [], "COPD exacerbation, tobacco dependence"),
    ("RW07", "outpatient", "F", 34, """CLINIC NOTE
34 year old female with moderate persistent asthma, using controller daily, now with acute exacerbation after URI.
Wheezing on exam, peak flow 60% of personal best.
Assessment: Moderate persistent asthma with acute exacerbation.
Plan: Oral steroids, albuterol nebulizer in clinic, follow up in 1 week.""",
     ["J45.41"], [], "asthma severity + exacerbation combination"),
    ("RW08", "outpatient", "F", 28, """PRENATAL VISIT
28 y/o G1P0 at 20 weeks gestation for routine prenatal care. No complaints. No bleeding, no contractions.
Fetal heart tones 148. Fundal height appropriate.
Assessment: Supervision of normal first pregnancy, second trimester. 20 weeks gestation.
Plan: Anatomy ultrasound scheduled. Return in 4 weeks.""",
     ["Z34.02", "Z3A.20"], [], "I.C.15 / Z34 normal pregnancy + Z3A weeks"),
    ("RW09", "ED", "F", 61, """EMERGENCY DEPARTMENT NOTE
61-year-old female tripped over a rug at home and fell on her outstretched right hand. Wrist pain and deformity.
X-ray: Displaced Colles fracture of the right distal radius. Skin intact.
Assessment: Closed displaced Colles fracture, right radius, initial encounter.
Plan: Closed reduction, splint, orthopedics follow-up.""",
     ["S52.531A"], ["W18.09XA", "W01.0XXA", "W19.XXXA", "Y92.009", "Y92.002", "Y93.89", "X58.XXXA", "W22.8XXA",
                    "W18.30XA", "W18.39XA", "W18.40XA"],
     "fracture 7th character A; external cause codes optional"),
    ("RW10", "outpatient", "M", 23, """URGENT CARE NOTE
23 y/o male cut his left forearm with a kitchen knife while cooking 1 hour ago.
Exam: 3 cm linear laceration, left volar forearm, no foreign body, tendons intact.
Procedure: Simple repair with 6 sutures.
Assessment: Laceration of left forearm without foreign body, initial encounter.""",
     ["S51.812A"], ["W26.0XXA", "Y92.010", "Y93.G3", "Y92.000"], "injury laterality and 7th character"),
    ("RW11", "inpatient", "M", 66, """DISCHARGE SUMMARY
66-year-old man with hypertension admitted for acute on chronic systolic heart failure exacerbation after dietary
sodium indiscretion. EF 25%. Diuresed 4 L.
Discharge diagnoses:
1. Acute on chronic systolic heart failure
2. Hypertension""",
     ["I11.0", "I50.23"], [], "I.C.9.a.1 hypertension with heart failure presumed causal -> I11.0"),
    ("RW12", "outpatient", "F", 19, """ENDOCRINOLOGY NOTE
19 y/o woman with type 1 diabetes on insulin pump reports two episodes of low blood sugar this week (glucose 52 and 58),
treated with juice. No loss of consciousness.
Assessment: Type 1 diabetes mellitus with hypoglycemia without coma.
Plan: Adjust basal rates overnight, continuous glucose monitor alerts set.""",
     ["E10.649"], ["Z79.4", "Z96.41"], "type 1 diabetes with hypoglycemia"),
    ("RW13", "outpatient", "F", 47, """BEHAVIORAL HEALTH FOLLOW-UP
47 year old female with major depressive disorder, recurrent episode, currently moderate severity. Sleep and appetite
reduced. Denies suicidal ideation.
Assessment: Major depressive disorder, recurrent, moderate.
Plan: Increase sertraline to 100 mg, continue therapy.""",
     ["F33.1"], [], "MDD episode/severity"),
    ("RW14", "outpatient", "M", 55, """SLEEP MEDICINE FOLLOW-UP
55 y/o male with obstructive sleep apnea, using CPAP nightly with good adherence.
Vitals: BMI 36.4.
Assessment:
1. Obstructive sleep apnea
2. Class 2 obesity, BMI 36.4
Plan: Continue CPAP, weight management referral.""",
     ["G47.33", "E66.812", "Z68.36"], [], "FY2025+ obesity class codes, BMI with associated diagnosis"),
    ("RW15", "outpatient", "F", 71, """INTERNAL MEDICINE VISIT
71-year-old woman with paroxysmal atrial fibrillation on apixaban long-term, mixed hyperlipidemia and hypothyroidism.
Medications: Apixaban 5 mg BID, rosuvastatin 20 mg, levothyroxine 75 mcg.
ROS: No palpitations, no bleeding.
Assessment:
1. Paroxysmal atrial fibrillation
2. Mixed hyperlipidemia
3. Hypothyroidism
Plan: Continue anticoagulation, lipid panel and TSH today.""",
     ["I48.0", "Z79.01", "E78.2", "E03.9"], [], "long-term anticoagulant Z79.01"),
    ("RW16", "inpatient", "F", 80, """DISCHARGE SUMMARY
80 y/o woman admitted with fever, myalgia and cough. Rapid PCR positive for influenza A. Chest x-ray shows pneumonia.
Treated with oseltamivir and IV fluids.
Discharge diagnosis: Influenza A with pneumonia.""",
     ["J10.00"], [], "I.C.10.c influenza confirmed + pneumonia combination code"),
    ("RW17", "outpatient", "M", 82, """GERIATRIC CLINIC
82-year-old man with Alzheimer's disease, late onset, with dementia. Family reports memory decline; no agitation,
no behavioral disturbance, no psychotic symptoms. Severity not specified.
Assessment: Late onset Alzheimer's disease with dementia, without behavioral disturbance.
Plan: Continue donepezil, caregiver support resources.""",
     ["G30.1", "F02.80"], [], "etiology/manifestation: G30.1 then F02.80"),
    ("RW18", "inpatient", "M", 69, """DISCHARGE SUMMARY
69 y/o male with chronic kidney disease stage 3a admitted with poor oral intake, dehydration and creatinine rise from
1.5 to 2.8. Sodium 128.
Discharge diagnoses:
1. Acute kidney injury on chronic kidney disease stage 3a
2. Dehydration
3. Hyponatremia
Creatinine returned to baseline with IV fluids.""",
     ["N17.9", "N18.31", "E86.0", "E87.1"], [], "AKI + CKD both coded (I.C.14)"),
    ("RW19", "outpatient", "F", 58, """CLINIC NOTE
58-year-old woman with burning on urination and frequency for 2 days. No fever, no flank pain.
UA: positive leukocyte esterase and nitrites.
Assessment: Urinary tract infection.
Plan: Nitrofurantoin for 5 days.""",
     ["N39.0"], [], "I.B.5 symptoms integral to a diagnosis are not coded"),
    ("RW20", "outpatient", "M", 50, """ENDOSCOPY REPORT
Indication: Screening colonoscopy. 50 y/o male, average risk except father had colon cancer at age 68.
Findings: Normal colon to the cecum. No polyps.
Impression: Normal screening colonoscopy.
Diagnoses: Encounter for screening colonoscopy; family history of colon cancer.""",
     ["Z12.11", "Z80.0"], [], "screening encounter + family history"),
    ("RW21", "outpatient", "M", 63, """WOUND CLINIC
63 y/o man with type 2 diabetes on insulin glargine and metformin presents with a diabetic ulcer of the right heel,
limited to breakdown of the skin, 1.2 cm.
Assessment:
1. Type 2 diabetes mellitus with diabetic foot ulcer, right heel, limited to breakdown of skin
Plan: Offloading boot, wound care, podiatry follow-up.""",
     ["E11.621", "L97.411", "Z79.4", "Z79.84"], [], "I.C.4.a.3 insulin AND oral -> Z79.4 + Z79.84; ulcer site/depth"),
    ("RW22", "ED", "F", 41, """EMERGENCY DEPARTMENT NOTE
41 y/o woman with substernal chest pain for 2 hours, non-exertional. ECG normal sinus rhythm.
Troponin negative x2. Myocardial infarction ruled out.
Assessment: Chest pain, unspecified. MI ruled out.
Plan: Outpatient stress test.""",
     ["R07.9"], [], "ruled-out condition is not coded"),
    ("RW23", "inpatient", "M", 35, """OPERATIVE / DISCHARGE SUMMARY
35-year-old man with right lower quadrant abdominal pain, nausea and leukocytosis. CT confirms acute appendicitis
without perforation or abscess. Laparoscopic appendectomy performed without complication.
Final diagnosis: Acute appendicitis.""",
     ["K35.80"], ["K35.890"], "integral symptoms (RLQ pain) not coded"),
    ("RW24", "outpatient", "M", 76, """CARDIOLOGY FOLLOW-UP
76 y/o male with coronary artery disease of native coronary arteries, no angina. History of myocardial infarction
in 2019 treated with PCI and stent to the LAD. Former smoker, quit 2010.
Assessment:
1. Coronary artery disease, native vessel, without angina
2. Old myocardial infarction
3. Status post coronary stent
4. Former tobacco use
Plan: Continue aspirin and statin.""",
     ["I25.10", "I25.2", "Z95.5", "Z87.891"], [], "history codes: old MI, stent status, former smoker"),
    ("RW25", "outpatient", "F", 68, """PRIMARY CARE VISIT
68-year-old woman with type 2 diabetes on metformin, with numbness and tingling of both feet. Monofilament decreased
bilaterally. History of right breast cancer treated with mastectomy in 2015, no current treatment, no evidence of
recurrence.
Assessment:
1. Type 2 diabetes mellitus with diabetic polyneuropathy
2. Personal history of breast cancer
Plan: Continue metformin, start gabapentin.""",
     ["E11.42", "Z79.84", "Z85.3"], ["Z90.11"], "history of malignancy no current treatment -> Z85"),
    ("RW26", "inpatient", "M", 78, """DISCHARGE SUMMARY
78 y/o right-handed man admitted with urinary tract infection. Past history of left MCA cerebral infarction 2 years
ago with residual right-sided hemiparesis.
Discharge diagnoses:
1. Urinary tract infection
2. Right hemiparesis as a late effect of prior cerebral infarction (right dominant side)""",
     ["N39.0", "I69.351"], [], "I.C.9.d sequelae of stroke -> I69, not Z86.73"),
    ("RW27", "outpatient", "F", 30, """CLINIC NOTE
30 y/o female with heartburn after meals. Endoscopy last year showed no esophagitis. Also has mild intermittent asthma,
uses albuterol rarely, no recent symptoms.
Assessment:
1. Gastroesophageal reflux disease without esophagitis
2. Mild intermittent asthma, uncomplicated
Plan: Omeprazole 20 mg daily.""",
     ["K21.9", "J45.20"], [], "coexisting chronic conditions"),
    ("RW28", "inpatient", "M", 59, """DISCHARGE SUMMARY
59-year-old man with type 2 diabetes on long-term insulin admitted with nausea, vomiting and glucose 512, anion gap 24,
positive ketones. Creatinine 2.1 from baseline 1.0.
Discharge diagnoses:
1. Diabetic ketoacidosis without coma, type 2 diabetes
2. Acute kidney injury""",
     ["E11.10", "N17.9", "Z79.4"], [], "DKA type 2 combination, Z79.4 long-term insulin"),
    ("RW29", "outpatient", "M", 44, """SPINE CLINIC
44 y/o man with low back pain radiating down the left leg below the knee for 3 weeks.
Exam: Positive straight leg raise on the left.
Assessment: Low back pain with left-sided sciatica.
Plan: Physical therapy, NSAIDs.""",
     ["M54.42"], [], "combination code lumbago with sciatica + laterality"),
    ("RW30", "outpatient", "F", 39, """ANNUAL PHYSICAL
39 y/o female for annual wellness examination. No complaints. Denies chest pain or shortness of breath.
No history of diabetes or hypertension. Mother had breast cancer at age 52.
Exam: Normal. Labs: normal.
Assessment: General adult medical examination without abnormal findings. Family history of breast cancer.
Plan: Screening mammogram at 40.""",
     ["Z00.00", "Z80.3"], [], "preventive visit + family history; negated conditions not coded"),
    ("RW31", "outpatient", "F", 72, """NEPHROLOGY VISIT
72 y/o woman with hypertension and chronic kidney disease stage 4. Hemoglobin 9.1, iron studies replete.
Assessment:
1. Hypertensive chronic kidney disease, stage 4
2. Anemia in chronic kidney disease
Plan: Start erythropoiesis-stimulating agent.""",
     ["I12.9", "N18.4", "D63.1"], [], "anemia in CKD D63.1 with CKD stage"),
]


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "data" / "evaluation" / "realworld.jsonl"
    with out.open("w") as fh:
        for cid, enc, sex, age, note, gold, optional, focus in CASES:
            fh.write(json.dumps({
                "document_id": cid,
                "clinical_text": note,
                "encounter_type": "outpatient" if enc == "ED" else enc,
                "patient_sex": sex,
                "patient_age": age,
                "gold_codes": [{"system": "ICD-10-CM", "code": c} for c in gold],
                "optional_codes": optional,
                "guideline_focus": focus,
                "synthetic": True,
            }) + "\n")
    print(f"wrote {len(CASES)} cases to {out}")


if __name__ == "__main__":
    main()
