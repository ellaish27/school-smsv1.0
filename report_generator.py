"""
Report card data generator for Holy Cross Lake View SSS.
Returns structured data ready for HTML template rendering.
"""
from collections import defaultdict
from datetime import date

GRADE_TABLE = [
    (80, 'A', 'EXCEPTIONAL ACHIEVEMENT',         5),
    (70, 'B', 'OUTSTANDING PERFORMANCE',        4),
    (60, 'C', 'SATISFACTORY PERFORMANCE',       3),
    (50, 'D', 'BASIC UNDERSTANDING',            2),
    (0,  'E', 'ELEMENTARY UNDERSTANDING',       1),
]


def get_grade(score):
    if score is None:
        return 'X', 'NOT TAKEN', 7
    for thr, g, d, p in GRADE_TABLE:
        if score >= thr:
            return g, d, p
    return 'O', 'FAIL', 6


def calc_final(bot, mt, eot):
    scores = [s for s in (bot, mt) if s is not None]
    mot = sum(scores) / len(scores) if scores else None
    if mot is None and eot is None:
        return None, None
    if mot is None:
        return None, eot
    if eot is None:
        return mot, mot
    return mot, mot * 0.5 + eot * 0.5


def generate_report_data(data: dict):
    """
    Generate report data for HTML template rendering.
    
    Input dict should contain:
      - student: dict with student info
      - marks: list of mark rows from DB
      - term: int (1, 2, or 3)
      - year: int
      - report_type: 'BOT' or 'EOT'
      - ct_comment: class teacher comment
      - ht_comment: head teacher comment
      - prev_avgs: {term_no: {paper_code: score}} for CA summary
      - subj_comments: {paper_code: comment}
      - photo_path: path to student photo (optional)
    
    Returns dict ready for template rendering.
    """
    student = data['student']
    marks_rows = data['marks']
    term = int(data['term'])
    year = int(data['year'])
    report_type = data.get('report_type', 'EOT')
    ct_comment = data.get('ct_comment', '')
    ht_comment = data.get('ht_comment', '')
    prev_avgs = data.get('prev_avgs', {})
    subj_comments = data.get('subj_comments', {})
    photo_path = data.get('photo_path')
    
    # Build marks matrix
    papers = defaultdict(lambda: {'BOT': None, 'MT': None, 'EOT': None,
                                  'subject_name': '', 'paper_code': ''})
    for row in marks_rows:
        key = row['paper_code']
        papers[key]['subject_name'] = row['subject_name']
        papers[key]['paper_code'] = row['paper_code']
        papers[key][row['exam_type']] = row['score']
    
    # Build marks display rows
    marks_display = []
    all_finals = []
    all_points = []
    
    for code in sorted(papers.keys(), key=lambda c: papers[c]['subject_name']):
        pm = papers[code]
        bot = pm['BOT']
        mt = pm['MT']
        eot = pm['EOT']
        _, final = calc_final(bot, mt, eot)
        grade, desc, pts = get_grade(final)
        
        row = {
            'paper_code': code,
            'subject_name': pm['subject_name'],
            'bot': bot,
            'eot': eot,
            'grade': grade,
            'desc': desc,
            'points': pts,
            'remark': subj_comments.get(code, ''),
        }
        
        if report_type == 'BOT':
            row['t_avg'] = None
            row['t1_avg'] = None
            row['t2_avg'] = None
            row['yr_avg'] = None
            if bot is not None:
                all_finals.append(bot)
                all_points.append(pts)
        else:  # EOT
            if final is not None:
                all_finals.append(final)
                all_points.append(pts)
            
            if term == 1:
                row['t_avg'] = final
                row['t1_avg'] = None
                row['t2_avg'] = None
                row['yr_avg'] = None
            elif term == 2:
                t1_prev = prev_avgs.get(1, {}).get(code)
                row['t_avg'] = final
                row['t1_avg'] = t1_prev
                row['t2_avg'] = None
                row['yr_avg'] = None
            else:  # term == 3
                t1_prev = prev_avgs.get(1, {}).get(code)
                t2_prev = prev_avgs.get(2, {}).get(code)
                term_vals = [v for v in [t1_prev, t2_prev, final] if v is not None]
                yr_avg = sum(term_vals) / len(term_vals) if term_vals else None
                row['t_avg'] = final
                row['t1_avg'] = t1_prev
                row['t2_avg'] = t2_prev
                row['yr_avg'] = yr_avg
        
        marks_display.append(row)
    
    # Summary
    avg = sum(all_finals) / len(all_finals) if all_finals else None
    total_pts = sum(all_points)
    avg_grade, avg_desc, _ = get_grade(avg)
    
    summary = {
        'avg': avg,
        'avg_grade': avg_grade,
        'avg_desc': avg_desc,
        'total_pts': total_pts,
        'num_subjects': len(papers),
    }
    
    # Previous averages for CA summary
    ca_summary = {}
    for t in range(1, term):
        avgs = prev_avgs.get(t, {})
        if avgs:
            scores = list(avgs.values())
            t_avg = sum(scores) / len(scores)
            t_gr, _, _ = get_grade(t_avg)
            ca_summary[t] = {
                'avg': f'{t_avg:.1f}',
                'grade': t_gr,
            }
    
    return {
        'student': student,
        'term': term,
        'year': year,
        'report_type': report_type,
        'ct_comment': ct_comment,
        'ht_comment': ht_comment,
        'marks_rows': marks_display,
        'summary': summary,
        'prev_avgs': ca_summary,
        'photo_url': None,  # will be set by caller if needed
        'generated_date': str(date.today()),
    }
