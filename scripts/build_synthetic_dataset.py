"""Build the synthetic evaluation dataset.

All notes are SYNTHETIC (written for this project; no real patients). Gold codes follow the
ICD-10-CM FY2026 Official Guidelines and were verified against the CDC tabular list.
Splits: dev (prompt/rule iteration), validation (confidence calibration & thresholds),
test (final reporting only - keep untouched).

Usage: python scripts/build_synthetic_dataset.py  -> data/evaluation/{dev,validation,test}.jsonl + data/raw/*.txt
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

NOTES: list[dict] = [
    # ------------------------------------------------------------------ dev
    {"id": "DOC001", "split": "dev", "encounter": "outpatient", "sex": "M", "age": 58,
     "gold": ["E11.9", "I10", "Z79.84"], "text": """PRIMARY CARE FOLLOW-UP
Chief Complaint: Follow-up of diabetes and blood pressure.
HPI: 58-year-old male with type 2 diabetes mellitus and hypertension presents for routine follow-up. Home glucose readings 110-140. Denies chest pain, shortness of breath, polyuria or blurred vision. No evidence of diabetic complications on recent eye and foot exams.
Medications: Metformin 1000 mg twice daily. Lisinopril 20 mg daily.
Vitals: BP 128/78, HR 72, BMI 27.1.
Physical Exam: Monofilament sensation intact bilaterally. Lungs clear.
Labs: HbA1c 6.8%. Urine microalbumin normal. eGFR 88.
Assessment:
1. Type 2 diabetes mellitus without complications - continue metformin
2. Essential hypertension - well controlled on lisinopril
Plan: Recheck HbA1c in 3 months. Continue current regimen."""},
    {"id": "DOC002", "split": "dev", "encounter": "outpatient", "sex": "F", "age": 64,
     "gold": ["E11.65", "Z79.4", "E78.5"], "text": """ENDOCRINOLOGY VISIT
Chief Complaint: Elevated blood sugars.
HPI: 64-year-old female with type 2 diabetes on insulin glargine 30 units nightly reports fasting glucose 220-260 over the past month. She also has hyperlipidemia on atorvastatin. She denies hypoglycemic episodes, numbness or visual changes.
Medications: Insulin glargine 30 units at bedtime. Atorvastatin 40 mg daily.
Labs: HbA1c 9.4%. LDL 128.
Assessment:
1. Type 2 diabetes mellitus with hyperglycemia
2. Hyperlipidemia, unspecified
Plan: Increase insulin glargine to 34 units. Diabetes education referral. Continue atorvastatin."""},
    {"id": "DOC003", "split": "dev", "encounter": "outpatient", "sex": "F", "age": 23,
     "gold": ["J45.20", "J30.9"], "text": """CLINIC NOTE
Chief Complaint: Asthma check.
HPI: 23-year-old female with mild intermittent asthma uses albuterol about once a month, mainly with exercise. No nighttime awakenings. Also reports seasonal sneezing and nasal congestion consistent with allergic rhinitis, controlled with fluticasone nasal spray.
Physical Exam: No wheezing. Normal work of breathing.
Assessment:
1. Mild intermittent asthma, uncomplicated
2. Allergic rhinitis
Plan: Continue albuterol as needed. Continue fluticasone nasal spray."""},
    {"id": "DOC004", "split": "dev", "encounter": "outpatient", "sex": "M", "age": 67,
     "gold": ["J44.1", "F17.210"], "text": """URGENT CARE NOTE
Chief Complaint: Worsening shortness of breath.
HPI: 67-year-old male with COPD presents with 3 days of increased dyspnea, cough and sputum production. He is a current smoker, one pack of cigarettes per day for 40 years. Denies fever or chest pain.
Physical Exam: Diffuse expiratory wheezing. SpO2 91% on room air.
Imaging: Chest x-ray shows hyperinflation, no infiltrate.
Assessment:
1. COPD with acute exacerbation
2. Nicotine dependence, cigarettes
Plan: Prednisone 40 mg daily for 5 days, azithromycin, albuterol nebulizer. Smoking cessation counseling provided."""},
    {"id": "DOC005", "split": "dev", "encounter": "outpatient", "sex": "F", "age": 31,
     "gold": ["N30.00"], "text": """CLINIC NOTE
Chief Complaint: Burning with urination.
HPI: 31-year-old female with 2 days of dysuria and urinary frequency. Denies fever, chills, flank pain or hematuria. Not pregnant.
Physical Exam: Mild suprapubic tenderness. No CVA tenderness.
Labs: Urinalysis positive for leukocyte esterase and nitrites. No blood on dipstick.
Assessment:
1. Acute cystitis without hematuria. Pyelonephritis unlikely given no fever or flank pain.
Plan: Nitrofurantoin 100 mg twice daily for 5 days. Return if fever develops."""},
    {"id": "DOC006", "split": "dev", "encounter": "outpatient", "sex": "M", "age": 12,
     "gold": ["J02.0"], "text": """PEDIATRIC SICK VISIT
Chief Complaint: Sore throat and fever.
HPI: 12-year-old male with 2 days of sore throat, fever to 101.8 F and painful swallowing. No cough or rhinorrhea.
Physical Exam: Tonsillar exudates, tender anterior cervical lymph nodes.
Labs: Rapid strep test positive.
Assessment:
1. Streptococcal pharyngitis
Plan: Amoxicillin 500 mg twice daily for 10 days. School note provided."""},
    {"id": "DOC007", "split": "dev", "encounter": "outpatient", "sex": "F", "age": 45,
     "gold": ["J06.9"], "text": """CLINIC NOTE
Chief Complaint: Cold symptoms.
HPI: 45-year-old female with 4 days of nasal congestion, sore throat and mild cough. No shortness of breath. Denies fever.
Physical Exam: Mild pharyngeal erythema without exudate. Lungs clear.
Assessment:
1. Acute upper respiratory infection
Plan: Supportive care, fluids, saline nasal spray. Return if symptoms worsen."""},
    {"id": "DOC008", "split": "dev", "encounter": "outpatient", "sex": "M", "age": 40,
     "gold": ["M54.50"], "text": """CLINIC NOTE
Chief Complaint: Back pain.
HPI: 40-year-old male with 1 week of low back pain after moving furniture. Pain does not radiate. Denies leg numbness, weakness, bowel or bladder incontinence.
Physical Exam: Paraspinal lumbar tenderness. Negative straight leg raise bilaterally.
Assessment:
1. Low back pain
Plan: Ibuprofen 600 mg three times daily with food, heat, physical therapy referral."""},
    {"id": "DOC009", "split": "dev", "encounter": "outpatient", "sex": "F", "age": 69,
     "gold": ["M17.11"], "text": """ORTHOPEDIC CONSULTATION
Chief Complaint: Right knee pain.
HPI: 69-year-old female with chronic right knee pain worse with stairs. No injury. Left knee is asymptomatic.
Physical Exam: Right knee crepitus, medial joint line tenderness, no effusion.
Imaging: X-ray right knee shows medial joint space narrowing and osteophytes.
Assessment:
1. Primary osteoarthritis of right knee
Plan: Weight-bearing exercises, acetaminophen, consider corticosteroid injection."""},
    {"id": "DOC010", "split": "dev", "encounter": "outpatient", "sex": "F", "age": 52,
     "gold": ["K21.9", "E03.9"], "text": """PROGRESS NOTE
Chief Complaint: Heartburn.
HPI: 52-year-old female with hypothyroidism on levothyroxine reports several months of postprandial heartburn and acid regurgitation. Denies dysphagia, weight loss or black stools.
Medications: Levothyroxine 75 mcg daily.
Labs: TSH 2.1.
Assessment:
1. Gastroesophageal reflux disease without esophagitis
2. Hypothyroidism - stable on levothyroxine
Plan: Omeprazole 20 mg daily, lifestyle modification. Continue levothyroxine."""},
    {"id": "DOC011", "split": "dev", "encounter": "outpatient", "sex": "F", "age": 34,
     "gold": ["F32.1", "F41.1"], "text": """BEHAVIORAL HEALTH VISIT
Chief Complaint: Low mood and worry.
HPI: 34-year-old female reports 2 months of depressed mood, anhedonia, poor sleep and low energy. This is her first depressive episode. PHQ-9 score 14. She also describes excessive worry about work and family most days for over a year. Denies suicidal ideation. No history of mania.
Assessment:
1. Major depressive disorder, single episode, moderate
2. Generalized anxiety disorder
Plan: Start sertraline 50 mg daily. Refer for cognitive behavioral therapy."""},
    {"id": "DOC012", "split": "dev", "encounter": "outpatient", "sex": "M", "age": 55,
     "gold": ["R07.9", "I10"], "text": """EMERGENCY DEPARTMENT NOTE
Chief Complaint: Chest pain.
HPI: 55-year-old male with hypertension presents with 2 hours of intermittent chest pain. No radiation. Denies diaphoresis or shortness of breath.
Physical Exam: Regular rhythm, no murmur. Lungs clear.
Studies: EKG normal sinus rhythm without ST changes. Initial troponin negative.
Assessment:
1. Chest pain, rule out myocardial infarction
2. Essential hypertension
Plan: Serial troponins, observation, stress test if negative."""},
    # ------------------------------------------------------------------ validation
    {"id": "DOC013", "split": "validation", "encounter": "outpatient", "sex": "M", "age": 72,
     "gold": ["I48.91", "Z79.01"], "text": """CARDIOLOGY FOLLOW-UP
Chief Complaint: Atrial fibrillation follow-up.
HPI: 72-year-old male with atrial fibrillation on apixaban for stroke prevention. Occasional palpitations. Denies bleeding, syncope or chest pain.
Medications: Apixaban 5 mg twice daily. Metoprolol 50 mg daily.
Physical Exam: Irregularly irregular rhythm, rate 78.
Assessment:
1. Atrial fibrillation - rate controlled
2. Long-term anticoagulation with apixaban
Plan: Continue apixaban and metoprolol. Follow up in 6 months."""},
    {"id": "DOC014", "split": "validation", "encounter": "outpatient", "sex": "F", "age": 78,
     "gold": ["I11.0", "I50.9"], "text": """CARDIOLOGY NOTE
Chief Complaint: Leg swelling and shortness of breath.
HPI: 78-year-old female with long-standing hypertension and heart failure reports increased lower extremity edema and dyspnea on exertion over two weeks. Denies chest pain.
Medications: Furosemide 20 mg daily, lisinopril 10 mg daily.
Physical Exam: Bibasilar crackles, 2+ bilateral lower extremity edema.
Assessment:
1. Hypertensive heart disease with heart failure
2. Heart failure, type not specified
Plan: Increase furosemide to 40 mg daily. Echocardiogram. Daily weights."""},
    {"id": "DOC015", "split": "validation", "encounter": "outpatient", "sex": "M", "age": 66,
     "gold": ["E11.22", "I12.9", "N18.31", "Z79.4"], "text": """NEPHROLOGY CONSULTATION
Chief Complaint: Chronic kidney disease.
HPI: 66-year-old male with type 2 diabetes mellitus on insulin and hypertension referred for declining kidney function. eGFR 52 and 49 over the last 6 months with moderately increased albuminuria.
Medications: Insulin glargine 20 units nightly, amlodipine 10 mg daily.
Assessment:
1. Type 2 diabetes mellitus with diabetic chronic kidney disease
2. Hypertensive chronic kidney disease
3. Chronic kidney disease stage 3a
Plan: Start empagliflozin. Renal diet education. Repeat BMP in 3 months."""},
    {"id": "DOC016", "split": "validation", "encounter": "outpatient", "sex": "F", "age": 47,
     "gold": ["D50.9", "Z80.0"], "text": """CLINIC NOTE
Chief Complaint: Fatigue.
HPI: 47-year-old female with fatigue for 3 months. Denies melena, hematochezia or abdominal pain. Family history of colon cancer in her father at age 52.
Labs: Hemoglobin 9.8, ferritin 6, MCV 72.
Assessment:
1. Iron deficiency anemia
2. Family history of colon cancer
Plan: Ferrous sulfate 325 mg daily. Refer for colonoscopy given family history."""},
    {"id": "DOC017", "split": "validation", "encounter": "outpatient", "sex": "F", "age": 29,
     "gold": ["G43.009"], "text": """NEUROLOGY NOTE
Chief Complaint: Headaches.
HPI: 29-year-old female with recurrent unilateral throbbing headaches with nausea and photophobia, 3 per month, each lasting a day. No visual aura. Headaches respond to sumatriptan.
Neurologic Exam: Normal.
Assessment:
1. Migraine without aura, not intractable, without status migrainosus
Plan: Continue sumatriptan as needed. Headache diary."""},
    {"id": "DOC018", "split": "validation", "encounter": "outpatient", "sex": "M", "age": 38,
     "gold": ["J01.90"], "text": """CLINIC NOTE
Chief Complaint: Facial pressure.
HPI: 38-year-old male with 10 days of nasal congestion, purulent nasal discharge and facial pressure. Symptoms initially improved and then worsened. Denies vision changes.
Physical Exam: Maxillary sinus tenderness.
Assessment:
1. Acute sinusitis
Plan: Amoxicillin-clavulanate for 7 days, saline irrigation."""},
    {"id": "DOC019", "split": "validation", "encounter": "outpatient", "sex": "F", "age": 26,
     "gold": ["S93.401A"], "text": """URGENT CARE NOTE
Chief Complaint: Right ankle injury.
HPI: 26-year-old female twisted her right ankle playing basketball today. Able to bear weight with pain. Initial encounter for this injury.
Physical Exam: Swelling and tenderness over the right lateral ankle. No bony tenderness at the malleoli.
Imaging: X-ray right ankle negative for fracture.
Assessment:
1. Sprain of right ankle
Plan: Rest, ice, compression, elevation. Ankle brace. Ibuprofen."""},
    {"id": "DOC020", "split": "validation", "encounter": "inpatient", "sex": "M", "age": 71,
     "gold": ["J18.9", "J96.01", "E11.9", "Z79.84"], "text": """DISCHARGE SUMMARY
Admission Diagnosis: Shortness of breath.
Hospital Course: 71-year-old male with type 2 diabetes mellitus admitted with fever, productive cough and hypoxia with SpO2 84% on room air requiring 4 L oxygen. Chest x-ray showed right lower lobe consolidation. Blood and sputum cultures without growth. Treated with ceftriaxone and azithromycin with improvement. Weaned to room air on day 4.
Discharge Medications: Metformin 500 mg twice daily, amoxicillin-clavulanate.
Discharge Diagnoses:
1. Pneumonia, unspecified organism
2. Acute respiratory failure with hypoxia
3. Type 2 diabetes mellitus without complications
Disposition: Home."""},
    {"id": "DOC021", "split": "validation", "encounter": "inpatient", "sex": "F", "age": 80,
     "gold": ["A41.9", "N39.0"], "text": """DISCHARGE SUMMARY
Hospital Course: 80-year-old female admitted with fever, hypotension and confusion. Lactate 3.2. Urinalysis consistent with infection; urine culture grew mixed flora. Blood cultures negative. Treated with IV fluids and ceftriaxone with resolution of hypotension.
Discharge Diagnoses:
1. Sepsis due to urinary tract infection
2. Urinary tract infection
Disposition: Skilled nursing facility."""},
    {"id": "DOC022", "split": "validation", "encounter": "outpatient", "sex": "M", "age": 44,
     "gold": ["E66.9", "Z68.32", "R73.03"], "text": """WELLNESS FOLLOW-UP
HPI: 44-year-old male for lab review. No complaints.
Vitals: BMI 32.4.
Labs: HbA1c 6.0%. Fasting glucose 112.
Assessment:
1. Obesity, BMI 32.4
2. Prediabetes
Plan: Lifestyle modification, nutrition referral, recheck HbA1c in 6 months."""},
    {"id": "DOC023", "split": "validation", "encounter": "outpatient", "sex": "F", "age": 60,
     "gold": ["E55.9"], "text": """CLINIC NOTE
HPI: 60-year-old female for review of labs. Mild fatigue. Denies bone pain or falls.
Labs: 25-hydroxy vitamin D 14 ng/mL.
Assessment:
1. Vitamin D deficiency
Plan: Vitamin D3 2000 IU daily. Recheck level in 3 months."""},
    {"id": "DOC024", "split": "validation", "encounter": "outpatient", "sex": "F", "age": 41,
     "gold": ["D50.9", "N92.0"], "text": """GYNECOLOGY NOTE
Chief Complaint: Heavy periods.
HPI: 41-year-old female with heavy menstrual bleeding with regular monthly cycles for 1 year, changing pads every 2 hours. Reports fatigue.
Labs: Hemoglobin 10.1, ferritin 8.
Assessment:
1. Menorrhagia with regular cycle
2. Iron deficiency anemia
Plan: Pelvic ultrasound. Oral iron. Consider hormonal therapy."""},
    # ------------------------------------------------------------------ test
    {"id": "DOC025", "split": "test", "encounter": "outpatient", "sex": "M", "age": 68,
     "gold": ["N40.1", "R35.1"], "text": """UROLOGY NOTE
Chief Complaint: Nighttime urination.
HPI: 68-year-old male with nocturia 3 times nightly and weak stream. No hematuria. Denies dysuria.
Physical Exam: Enlarged smooth prostate without nodules.
Labs: PSA 2.1.
Assessment:
1. Benign prostatic hyperplasia with lower urinary tract symptoms
2. Nocturia
Plan: Start tamsulosin 0.4 mg nightly."""},
    {"id": "DOC026", "split": "test", "encounter": "outpatient", "sex": "F", "age": 19,
     "gold": ["A08.4", "E86.0"], "text": """URGENT CARE NOTE
Chief Complaint: Vomiting and diarrhea.
HPI: 19-year-old female with 1 day of vomiting and watery diarrhea after roommates had similar illness. No blood in stool. Denies abdominal pain.
Physical Exam: Dry mucous membranes, tachycardic at 108.
Assessment:
1. Viral gastroenteritis
2. Dehydration
Plan: IV fluids 1 liter, ondansetron, oral rehydration."""},
    {"id": "DOC027", "split": "test", "encounter": "outpatient", "sex": "M", "age": 57,
     "gold": ["L03.116"], "text": """CLINIC NOTE
Chief Complaint: Red swollen leg.
HPI: 57-year-old male with 3 days of redness, warmth and swelling of the left lower leg. No trauma. Denies fever. No history of DVT.
Physical Exam: Erythema and warmth of left anterior shin, no fluctuance.
Assessment:
1. Cellulitis of left lower limb
Plan: Cephalexin 500 mg four times daily for 7 days. Mark borders, return if spreading."""},
    {"id": "DOC028", "split": "test", "encounter": "outpatient", "sex": "F", "age": 63,
     "gold": ["E03.9", "E78.5"], "text": """PROGRESS NOTE
HPI: 63-year-old female with hypothyroidism and hyperlipidemia for annual medication review. Denies palpitations, weight change or muscle aches.
Medications: Levothyroxine 100 mcg daily, simvastatin 20 mg daily.
Labs: TSH 3.0. LDL 96.
Assessment:
1. Hypothyroidism
2. Hyperlipidemia
Plan: Continue current medications."""},
    {"id": "DOC029", "split": "test", "encounter": "outpatient", "sex": "M", "age": 50,
     "gold": ["I10", "Z83.3"], "text": """CLINIC NOTE
Chief Complaint: Blood pressure follow-up.
HPI: 50-year-old male presents for follow-up of hypertension. No history of diabetes. Denies headache, chest pain or vision changes.
Family History: Mother with type 2 diabetes. Father with hypertension.
Labs: Fasting glucose 92. HbA1c 5.3%.
Assessment:
1. Essential hypertension
2. Family history of diabetes mellitus
Plan: Continue amlodipine 5 mg daily. Annual glucose screening."""},
    {"id": "DOC030", "split": "test", "encounter": "outpatient", "sex": "F", "age": 36,
     "gold": ["G44.209"], "text": """CLINIC NOTE
Chief Complaint: Headaches.
HPI: 36-year-old female with bilateral band-like headaches at the end of workdays, several times per week. No nausea, photophobia or aura. Headaches respond to acetaminophen.
Neurologic Exam: Normal.
Assessment:
1. Tension-type headache, not intractable
Plan: Stress management, ergonomic review, acetaminophen as needed."""},
    {"id": "DOC031", "split": "test", "encounter": "outpatient", "sex": "M", "age": 48,
     "gold": ["G47.00", "F41.9"], "text": """CLINIC NOTE
Chief Complaint: Trouble sleeping.
HPI: 48-year-old male with difficulty falling and staying asleep for 3 months associated with anxiety about finances. No snoring or witnessed apneas. Denies depressed mood.
Assessment:
1. Insomnia
2. Anxiety
Plan: Sleep hygiene counseling, CBT-I referral. Follow up in 4 weeks."""},
    {"id": "DOC032", "split": "test", "encounter": "outpatient", "sex": "M", "age": 70,
     "gold": ["M10.9", "I12.9", "N18.32"], "text": """PROGRESS NOTE
HPI: 70-year-old male with gout and hypertension with chronic kidney disease presents after a gout flare of the right great toe, now resolved. eGFR 38.
Medications: Allopurinol 100 mg daily, losartan 50 mg daily.
Assessment:
1. Gout
2. Hypertensive chronic kidney disease
3. Chronic kidney disease stage 3b
Plan: Continue allopurinol. Uric acid level. Renal function in 3 months."""},
    {"id": "DOC033", "split": "test", "encounter": "outpatient", "sex": "F", "age": 54,
     "gold": ["J20.9"], "text": """CLINIC NOTE
Chief Complaint: Cough.
HPI: 54-year-old female with 10 days of productive cough and chest congestion. No fever. Non-smoker.
Physical Exam: Scattered rhonchi, no focal consolidation.
Imaging: Chest x-ray negative for pneumonia.
Assessment:
1. Acute bronchitis. Pneumonia ruled out by chest x-ray.
Plan: Supportive care, honey, dextromethorphan. Return if fever or dyspnea."""},
    {"id": "DOC034", "split": "test", "encounter": "outpatient", "sex": "F", "age": 61,
     "gold": ["I10", "Z85.3"], "text": """ONCOLOGY SURVIVORSHIP / PRIMARY CARE VISIT
HPI: 61-year-old female with history of left breast cancer treated with mastectomy and chemotherapy 6 years ago, completed all treatment. No evidence of recurrence on surveillance imaging. Also has hypertension on lisinopril.
Assessment:
1. Personal history of breast cancer, no evidence of recurrence
2. Essential hypertension
Plan: Annual mammogram of right breast. Continue lisinopril."""},
    {"id": "DOC035", "split": "test", "encounter": "inpatient", "sex": "M", "age": 62,
     "gold": ["I21.4", "I25.10", "E78.5", "F17.210"], "text": """DISCHARGE SUMMARY
Hospital Course: 62-year-old male admitted with substernal chest pressure. Troponin rose from 0.08 to 1.9. EKG with ST depressions without ST elevation. Cardiac catheterization showed multivessel coronary artery disease and a drug-eluting stent was placed. He smokes 1 pack of cigarettes daily.
Discharge Diagnoses:
1. Non-ST elevation myocardial infarction (NSTEMI)
2. Coronary artery disease
3. Hyperlipidemia
4. Nicotine dependence, cigarettes
Discharge Medications: Aspirin, clopidogrel, atorvastatin 80 mg, metoprolol.
Disposition: Home with cardiac rehab referral."""},
    {"id": "DOC036", "split": "test", "encounter": "outpatient", "sex": "M", "age": 74,
     "gold": ["I25.10", "E11.9", "Z79.84"], "text": """CARDIOLOGY FOLLOW-UP
HPI: 74-year-old male with coronary artery disease and type 2 diabetes. No angina since stent placement 2 years ago. Denies dyspnea or edema.
Medications: Aspirin 81 mg, atorvastatin 40 mg, metformin 1000 mg twice daily.
Assessment:
1. Coronary artery disease without angina
2. Type 2 diabetes mellitus without complications
Plan: Continue current regimen. Lipid panel."""},
]


def main() -> None:
    out_dir = ROOT / "data" / "evaluation"
    raw_dir = ROOT / "data" / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    by_split: dict[str, list[dict]] = {}
    for n in NOTES:
        rec = {
            "document_id": n["id"], "clinical_text": n["text"], "encounter_type": n["encounter"],
            "patient_sex": n["sex"], "patient_age": n["age"],
            "gold_codes": [{"system": "ICD-10-CM", "code": c} for c in n["gold"]],
            "synthetic": True,
        }
        by_split.setdefault(n["split"], []).append(rec)
        (raw_dir / f"{n['id']}.txt").write_text(n["text"] + "\n")
    for split, recs in by_split.items():
        with (out_dir / f"{split}.jsonl").open("w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        print(f"{split}: {len(recs)} documents, {sum(len(r['gold_codes']) for r in recs)} gold codes")


if __name__ == "__main__":
    main()
