import sqlite3
import os

def run_migration():
    db_path = r'C:/Users/prati/OneDrive/Desktop/DigiPath/digipath.db'
    if not os.path.exists(db_path):
        print('Database not found.')
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # List of required migrations
    queries = [
        "ALTER TABLE users ADD COLUMN career_interests JSON DEFAULT '[]'",
        "ALTER TABLE predictions ADD COLUMN filters JSON DEFAULT '{}'",
        "ALTER TABLE resume_analyses ADD COLUMN ats_score FLOAT DEFAULT 0",
        "ALTER TABLE resume_analyses ADD COLUMN skill_score FLOAT DEFAULT 0",
        "ALTER TABLE resume_analyses ADD COLUMN project_score FLOAT DEFAULT 0",
        "ALTER TABLE resume_analyses ADD COLUMN keyword_score FLOAT DEFAULT 0",
        "ALTER TABLE resume_analyses ADD COLUMN education_score FLOAT DEFAULT 0",
        "ALTER TABLE resume_analyses ADD COLUMN improvement_suggestions JSON DEFAULT '[]'",
        "ALTER TABLE resume_analyses ADD COLUMN recommended_careers JSON DEFAULT '[]'",
        "ALTER TABLE resume_analyses ADD COLUMN recommended_job_roles JSON DEFAULT '[]'",
        "ALTER TABLE resume_analyses ADD COLUMN recommended_certifications JSON DEFAULT '[]'",
        "ALTER TABLE resume_analyses ADD COLUMN recommended_higher_studies JSON DEFAULT '[]'",
        "ALTER TABLE resume_analyses ADD COLUMN skill_gaps JSON DEFAULT '[]'",
        "ALTER TABLE resume_analyses ADD COLUMN learning_roadmap JSON DEFAULT '[]'",
        "ALTER TABLE resume_analyses ADD COLUMN industry_recommendations JSON DEFAULT '[]'"
    ]
    
    print('Running database migrations...')
    for q in queries:
        try:
            cursor.execute(q)
            print(f'SUCCESS: {q}')
        except sqlite3.OperationalError as e:
            if 'duplicate column name' in str(e).lower():
                # print(f'SKIPPED (Already exists): {q}')
                pass
            else:
                print(f'ERROR: {q} -> {e}')
                
    conn.commit()
    conn.close()
    print('Migration complete.')

if __name__ == '__main__':
    run_migration()
