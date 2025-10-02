from . import db
from .models import User, Pointstable, Fixture, Squad
import os, csv, re, pytz, requests, time
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Blueprint, jsonify, render_template, url_for, redirect, request, flash, Response, json, stream_with_context
from flask_login import login_required, current_user
from sqlalchemy import and_, or_
from sqlalchemy.sql import text
import requests
from bs4 import BeautifulSoup
from fuzzywuzzy import fuzz, process
from urllib.request import Request, urlopen
from datetime import time, datetime, date, timedelta

main = Blueprint('main', __name__)

tz = pytz.timezone('Asia/Kolkata')

pofs = {'SF1':'Semi Final 1', 'SF2':'Semi Final 2', 'F':'Final'}

liveURL_Prefix = "https://cmc2.sportskeeda.com/live-cricket-score/"
liveURL_Suffix = "/ajax"

full_name = {'AUS-W':'Australia Women',
             'BAN-W':'Bangladesh Women',
             'ENG-W':'England Women',
             'IND-W':'India Women',
             'NZ-W':'New Zealand Women',
             'PAK-W':'Pakistan Women',
             'SA-W':'South Africa Women',
             'SL-W':'Sri Lanka Women',
             'TBA':'TBA'}

teamID = {8652:['AUS-W','Australia Women'],
             10712:['BAN-W','Bangladesh Women'],
             9534:['ENG-W','England Women'],
             9536:['IND-W','India Women'],
             8650:['NZ-W','New Zealand Women'],
             10259:['PAK-W','Pakistan Women'],
             10279:['SA-W','South Africa Women'],
             10277:['SL-W','Sri Lanka Women'],
             127770:['TBA','TBA'],
             127775:['TBA','TBA']}

clr = {'AUS-W':{'c1':'#ffcc00', 'c2':'#006400', 'c3':'#ffd700'},
        'ENG-W':{'c1':'#00247d', 'c2':'#a9a9a9', 'c3':'#cf142b'},
        'NZ-W':{'c1':'#0d0d0d', 'c2':'#808080', 'c3':'#ffffff'},
        'SA-W':{'c1':'#006400', 'c2':'#ffcc00', 'c3':'#007a33'},
        'IND-W':{'c1':"#1a73e8", 'c2':"#ff9933", 'c3':"#0044cc"},
        'BAN-W':{'c1':"#006a4e", 'c2':'#f42a41', 'c3':'#b22222'},
        'PAK-W':{'c1':"#6FEBC2", 'c2':'#115740', 'c3':'#006400'},
        'SL-W':{'c1':"#1a237e", 'c2':"#ffb700", 'c3':"#003893"},
        'TBA':{'c1':'#ffffff', 'c2':'#ffffff', 'c3':'#ffffff'}}

ptclr = {'AUS-W':'#ffcc00',
        'ENG-W':'#00247d',
        'NZ-W':'#808080',
        'SA-W':'#007a33',
        'IND-W':"#1a73e8",
        'BAN-W':"#137932",
        'PAK-W':"#0bc941",
        'SL-W':"#ffb700"}

sqclr = {'AUS-W': {'c1': 'hsl(0 94% 52%)', 'c2': 'hsl(51 100% 52%)'},    # Red to Gold
    'ENG-W': {'c1': 'hsl(310 89% 52%)', 'c2': 'hsl(208 100% 31%)'},   # Pink to Blue
    'NZ-W': {'c1': 'hsl(225, 31%, 15%)', 'c2': 'hsl(44, 60%, 65%)'},   # Navy to Gold
    'IND-W': {'c1': 'hsl(310 89% 52%)', 'c2': 'hsl(208 100% 31%)'},   # Pink to Blue
    'BAN-W': {'c1': 'hsl(225, 31%, 15%)', 'c2': 'hsl(44, 60%, 65%)'},   # Navy to Gold
    'PAK-W': {'c1': 'hsl(310 89% 52%)', 'c2': 'hsl(208 100% 31%)'},   # Pink to Blue
    'SL-W': {'c1': 'hsl(225, 31%, 15%)', 'c2': 'hsl(44, 60%, 65%)'},   # Navy to Gold
    'SA-W': {'c1': 'hsl(195 89% 52%)', 'c2': 'hsl(32 95% 53%)'}     # Light Blue to Gold
}

def serialize(obj):
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize(i) for i in obj]
    elif isinstance(obj, (time, datetime, date)):
        return obj.isoformat()
    else:
        return obj

def normalize_name(name):
    """Normalize names for better matching"""
    # Remove special characters and extra spaces
    name = re.sub(r'[^a-zA-Z ]', '', name.lower()).strip()
    # Handle common name variations
    name = name.replace('mohd', 'mohammed').replace('md', 'mohammed')
    return ' '.join(sorted(name.split()))  # Sort name parts for order-independent matching


def find_player(full_name, player_data, threshold=80):
    """
    Find the best matching player in the database

    Args:
        full_name (str): Name to search for (e.g., "Akash Naman Singh")
        player_data (list): List of player tuples from database
        threshold (int): Minimum match score (0-100)

    Returns:
        tuple: Best matching player record or None
    """
    # Extract just the names from player data (3rd element in each tuple)
    player_names = [player[2] for player in player_data]

    # First try exact match
    normalized_search = normalize_name(full_name)
    for i, player in enumerate(player_data):
        if normalize_name(player[2]) == normalized_search:
            return player

    # Then try fuzzy matching with multiple strategies
    strategies = [
        (fuzz.token_set_ratio, "token set ratio"),
        (fuzz.token_sort_ratio, "token sort ratio"),
        (fuzz.partial_ratio, "partial ratio"),
        (fuzz.WRatio, "weighted ratio")
    ]

    best_match = None
    best_score = 0

    for player in player_data:
        db_name = player[2]
        for strategy, _ in strategies:
            score = strategy(full_name, db_name)
            if score > best_score:
                best_score = score
                best_match = player
                if best_score == 100:  # Perfect match
                    return best_match

    # Also check initials match (e.g., "A. N. Singh" vs "Akash Naman Singh")
    if best_score < threshold:
        search_initials = ''.join([word[0] for word in full_name.split() if len(word) > 1])
        for player in player_data:
            db_name = player[2]
            db_initials = ''.join([word[0] for word in db_name.split() if len(word) > 1 and word[0].isupper()])
            if db_initials and search_initials == db_initials:
                return player

    return best_match if best_score >= threshold else None

def get_data_from_url(url):
    headers = {
        'User-Agent' : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.447.124 Safari/537.36',
        'Accept-Language' : 'en-US,en;q=0.9',
    }
    req = Request(url, headers=headers)
    with urlopen(req) as response:
        html = response.read().decode('utf-8')
    SquadDT = (db.session.execute(text('SELECT * FROM Squad')).fetchall())
    if response.getcode() == 200:
        try:
            soup = BeautifulSoup(html, 'html.parser')
            thead = soup.find('thead', class_="cb-srs-gray-strip")
            if thead is None:
                return None

            headcells = soup.find_all('th')[1:]
            headers = []
            for i in headcells:
                headers.append(i.text.strip())
            tbody = soup.find('tbody')
            data = []
            for body in tbody.find_all('tr'):
                bodycells = body.find_all('td')[1:]
                d = {}
                for i, val in enumerate(bodycells):
                    if i == 0:
                        match = find_player(val.text.strip(), SquadDT)
                        d['Team'] = match[3] if match else "NA"
                        d[headers[i]] = match[2] if match else val.text.strip()
                    else:
                        d[headers[i]] = val.text.strip()
                data.append(d)
            return data
        except Exception:
            return None
    else:
        return None
    
def get_innings_data(matID):
    inn1 = requests.get(f"https://apiv2.cricket.com.au/web/views/comments?fixtureId={matID}&inningNumber=1&commentType=&overLimit=51&jsconfig=eccn%3Atrue&format=json", verify=False).json()
    inn2 = requests.get(f"https://apiv2.cricket.com.au/web/views/comments?fixtureId={matID}&inningNumber=2&commentType=&overLimit=51&jsconfig=eccn%3Atrue&format=json", verify=False).json()
    return inn1, inn2

def calculate_age(dob, current_date):
    # Calculate the number of full years
    years = current_date.year - dob.year
    has_birthday_passed = (current_date.month, current_date.day) >= (dob.month, dob.day)

    # Adjust the years if the birthday has not yet occurred this year
    if not has_birthday_passed:
        years -= 1

    # Calculate the last birthday date
    last_birthday = dob.replace(year=current_date.year) if has_birthday_passed else dob.replace(
        year=current_date.year - 1)

    current_date = current_date.date()

    # Calculate the number of days since the last birthday
    days = (current_date - last_birthday).days
    return str(years) + " years " + str(days) + " days"

def oversAdd(a, b):
    A, B = round(int(a)*6 + (a-int(a))*10, 0), round(int(b)*6 + (b-int(b))*10, 2)
    S = int(A) + int(B)
    s = S//6 + (S%6)/10
    return s

def oversSub(a, b):
    A, B = round(int(a) * 6 + (a - int(a)) * 10, 0), round(int(b) * 6 + (b - int(b)) * 10, 2)
    S = int(A) - int(B)
    s = S // 6 + (S % 6) / 10
    return s

def ovToPer(n):
    return (int(n)+((n-int(n))*10)/6)

def concat_DT(D, T):
    dttm = D.strftime('%Y-%m-%d')+' '+ \
                     T.strftime('%H:%M:%S')
    return datetime.strptime(dttm, '%Y-%m-%d %H:%M:%S')

def num_suffix(num):
    if num % 100 in [11, 12, 13]:
        return str(num) + "th"
    elif (num % 10) == 1:
        return str(num) + "st"
    elif (num % 10) == 2:
        return str(num) + "nd"
    elif (num % 10) == 3:
        return str(num) + "rd"
    else:
        return str(num) + "th"

def render_live_URL(tA, tB, mn, dt):
    teamAB = full_name[tA].replace(" ", "-").lower() + "-vs-" + full_name[tB].replace(" ", "-").lower()
    if mn.isdigit():
        matchNo = num_suffix(int(mn)) + "-match"
    elif "Semi Final" in mn:
        if tA != "TBA" and tB != "TBA":
            matchNo = num_suffix(int(mn.split()[-1])) + "-semi-final"
        else:
            matchNo = num_suffix(int(mn.split()[-1])) + "-semi-final-iccwwc"
    elif "Final" in mn:
        if tA != "TBA" and tB != "TBA":
            matchNo = "final"
        else:
            matchNo = "final-iccwwc"
    dt = dt.strftime("%d-%B-%Y").lower()
    URL = liveURL_Prefix + teamAB + "-" + matchNo + "-" + dt + liveURL_Suffix
    return URL

@main.route('/')
def index():
    if db.session.execute(text('select count(*) from user')).scalar() == 0:
        user = User(email='adminwwc2025@gmail.com', \
                    password=generate_password_hash('**************', method='pbkdf2:sha256', salt_length=8), \
                    name='AdminWWC2025')
        db.session.add(user)
        db.session.commit()
    if db.session.execute(text('select count(*) from pointstable')).scalar() == 0:
        teams = ['AUS-W', 'BAN-W', 'ENG-W', 'IND-W', 'NZ-W', 'PAK-W', 'SA-W', 'SL-W']
        inter = os.getcwd()
        for i in teams:
            tm = Pointstable(team_name=i, P=0,W=0,L=0,NR=0,\
                    Points=0, NRR=0.0, Win_List=str({}),\
                logo_path='{}/CWC/static/images/{}.png'.format(inter,i),\
                For={'runs':0, 'overs':0.0}, Against={'runs':0, 'overs':0.0})
            db.session.add(tm)
            db.session.commit()
    if db.session.execute(text('select count(*) from fixture')).scalar() == 0:
        df = open('CWC/CWC2025.csv', 'r')
        df = list(csv.reader(df))
        for i in df[1:]:
            mt = Fixture(Match_No=i[0], Date=(datetime.strptime(i[1],'%d-%m-%Y')).date(),\
                                    Time=(datetime.strptime(i[2],'%H.%M.%S')).time(),\
                                    Team_A=i[3], Team_B=i[4], Venue=i[5],\
                                    A_info={'runs':0, 'overs':0.0, 'wkts':0},\
                                    B_info={'runs':0, 'overs':0.0, 'wkts':0},\
                                    Match_ID=i[6])
            db.session.add(mt)
            db.session.commit()
    if db.session.execute(text('select count(*) from squad')).scalar() == 0:
        df = open('CWC/all teams squad cwc.csv', 'r')
        df = list(csv.reader(df))
        for i in df[1:]:
            pl = Squad(Player_ID=i[0], Name=i[1], Team=i[2], Captain=i[3], Keeper=i[4],\
                       Role=i[5], Batting=i[6], Bowling=i[7], Nationality=i[8],\
                       DOB=(datetime.strptime(i[9],'%d-%m-%Y')).date())
            db.session.add(pl)
            db.session.commit()
    return render_template('index.html', teams=list(full_name.keys()), clr=clr)

@main.route('/pointstable')
def displayPT():
    dataPT = Pointstable.query.order_by(Pointstable.Points.desc(),Pointstable.W.desc(),Pointstable.NRR.desc(),Pointstable.id.asc()).all()
    dt = [['#', 'Logo', 'Teams', 'P', 'W', 'L', 'NR', 'Points', 'NRR', 'Last 5', 'Next Match'], [i for i in range(1,11)],\
         [], [], [], [], [], [], [], [], [], [], []]
    teams_ABV = []
    for i in dataPT:
        img = "/static/images/{}.png".format(i.team_name)
        dataFR = db.session.execute(
    text('SELECT "Team_A", "Team_B", "Result" FROM Fixture WHERE "Team_A" = :team OR "Team_B" = :team order by id'),
                                                {'team': i.team_name}).fetchall()
        nm = '--'
        for j in dataFR:
            if j[2] != None:
                continue
            nm = j[0] if j[0] != i.team_name else j[1]
            nm = 'vs ' + nm
            break
        dt[2].append(img)
        teams_ABV.append(i.team_name)
        dt[3].append(full_name[i.team_name])
        dt[4].append(i.P)
        dt[5].append(i.W)
        dt[6].append(i.L)
        dt[7].append(i.NR)
        dt[8].append(i.Points)
        I = '{0:+}'.format(i.NRR)
        dt[9].append(I)
        wl = list(eval(i.Win_List).values())
        wl = wl if len(wl)<5 else wl[-5:]
        wl = list(wl)
        wl = ''.join(wl)
        dt[10].append(wl)
        dt[11].append(nm)
        dt[12].append(i.qed)
    return render_template('displayPT.html', PT=dt, TABV=teams_ABV, clr=ptclr)

@main.route('/fixtures')
def displayFR():
    team = request.args.get('fteam','All',type=str)
    if team == 'All':
        dataFR = db.session.execute(text('select * from Fixture order by id'))\
            #Fixture.query.all()
        hint = 'All'
    else:
        dataFR = db.session.execute(text('SELECT * FROM Fixture WHERE "Team_A" = :team OR "Team_B" = :team order by id'),{'team': team}).fetchall()
            #Fixture.query.filter_by(or_(Fixture.Team_A == team, Fixture.Team_B == team)).all()
        hint = team
    dt = [['Match No', 'Date', 'Venue', 'Team-A', 'Team-B', 'TA-Score', 'TB-Score', 'WT', 'WType', 'WBy', 'Result']]
    for i in dataFR:
        dtt = []
        dtt.append(i[1]) #Match No
        dttm = i[2].strftime('%Y-%m-%d')+' '+ \
                     i[3].strftime('%H:%M:%S')
        dtt.append(datetime.strptime(dttm, '%Y-%m-%d %H:%M:%S'))  #DateTime
        dtt.append(i[6].split(', ')[1])  #Venue
        dtt.append(i[4])  #Team A
        dtt.append(i[5])  #Team B
        A, B = i[8], i[9]
        dtt.append(A) #TA_Scr
        dtt.append(B) #TB_Scr
        if i[10] is None:
            dtt.append('TBA') #Win-Team
            dtt.append('TBA')
            dtt.append('TBA')
        elif i[10] == 'NA':
            dtt.append('NA')
            dtt.append('NA')
            dtt.append('NA')
            dtt.append(i[7])
        else:
            dtt.append(i[10])
            WType = 'wickets' if 'wickets' in i[7] else 'runs'
            dtt.append(WType)
            WBy = re.findall(r'\d+', i[7])[0]
            dtt.append(str(WBy))
            dtt.append(i[7][i[7].index('won'):])
        #dtt.append(i[7])
        dt.append(dtt)
    current_date = datetime.now(tz)
    current_date = current_date.replace(tzinfo=None)
    return render_template('displayFR.html', FR=dt, hint=hint, fn=full_name, current_date=current_date, clr=clr)

@main.route('/teams')
def teams():
    return render_template('teams.html', fn=full_name, clr=clr, sqclr=clr)

@main.route('/teams/<team>')
def squad(team):
    sq = Squad.query.filter_by(Team=team).order_by(Squad.Player_ID).all()
    return render_template('squad.html', team=team, sq=sq, fn=full_name[team], clr=clr[team], sqclr=clr[team])

@main.route('/team-<team>/squad_details/<name>')
def squad_details(team, name):
    sq = Squad.query.filter_by(Name=name).first()
    current_date = datetime.now(tz)
    current_date = current_date.replace(tzinfo=None)
    age = calculate_age(sq.DOB, current_date)
    return render_template('squad_details.html', sq=sq, clr=clr[team], team=team, age=age, sqclr=clr[team])

def get_matchInfo(match):
    MatchDT = db.session.execute(text('SELECT * FROM Fixture WHERE "Match_No" = :matchno'), {'matchno': match}).fetchall()
    MatchURL = render_live_URL(MatchDT[0][4], MatchDT[0][5], match, MatchDT[0][2])
    dttm = concat_DT(MatchDT[0][2], MatchDT[0][3])
    SquadFull = db.session.execute(text('SELECT * FROM Squad')).fetchall()
    response = requests.get(MatchURL, verify=False)
    MatchLDT = response.json()
    MatchDT2 = []
    MatchDT2.append(num_suffix(int(MatchDT[0][1]))+" Match" if MatchDT[0][1].isdigit() else MatchDT[0][1])
    MatchDT2.append(MatchDT[0][6].split(", ")[1])
    MatchDT2.append(num_suffix(MatchDT[0][2].day)+" "+MatchDT[0][2].strftime("%B %Y"))
    current_date = datetime.now(tz)
    current_date = current_date.replace(tzinfo=None)
    MatchDT = [dict(row._mapping) for row in MatchDT]
    return serialize({'match': match, 'cd': current_date, 'dt1': MatchDT, 'dt2': MatchDT2, 'dt3': MatchLDT, 'tid': teamID, 'dttm': dttm})

@main.route('/match-<match>/matchInfo')
def matchInfo(match):
    source = request.args.get('source', None)
    team = request.args.get('fteam', None)
    return render_template('info.html', match=match, source=source, fteam=team)

def get_matchOvers(match):
    MatchDT = db.session.execute(text('SELECT * FROM Fixture WHERE "Match_No" = :matchno'), {'matchno': match}).fetchall()
    MatchURL = render_live_URL(MatchDT[0][4], MatchDT[0][5], match, MatchDT[0][2])
    Inn1, Inn2 = get_innings_data(MatchDT[0][11])
    dttm = concat_DT(MatchDT[0][2], MatchDT[0][3])
    response = requests.get(MatchURL, verify=False)
    MatchLDT = response.json()
    MatchDT2 = []
    MatchDT2.append(num_suffix(int(MatchDT[0][1]))+" Match" if MatchDT[0][1].isdigit() else MatchDT[0][1])
    MatchDT2.append(MatchDT[0][6].split(", ")[1])
    MatchDT2.append(num_suffix(MatchDT[0][2].day)+" "+MatchDT[0][2].strftime("%B %Y"))
    current_date = datetime.now(tz)
    current_date = current_date.replace(tzinfo=None)
    MatchDT = [dict(row._mapping) for row in MatchDT]
    return serialize({'match':match, 'cd':current_date, 'dt1':MatchDT, 'dt2':MatchDT2, 'dt3':MatchLDT, 'tid':teamID, 'dttm':dttm, 'inn1':Inn1, 'inn2':Inn2, 'clr':clr})

@main.route('/match-<match>/Overs')
def matchOvers(match):
    source = request.args.get('source', None)
    team = request.args.get('fteam', None)
    return render_template('overs.html', match=match, source=source, fteam=team)

def get_liveScore(match):
    MatchDT = db.session.execute(text('SELECT * FROM Fixture WHERE "Match_No" = :matchno'),{'matchno': match}).fetchall()
    SquadFull = (db.session.execute(text('SELECT * FROM Squad')).fetchall())
    MatchURL = render_live_URL(MatchDT[0][4], MatchDT[0][5], match, MatchDT[0][2])
    Inn1, Inn2 = get_innings_data(MatchDT[0][11])
    dttm = concat_DT(MatchDT[0][2], MatchDT[0][3])
    response = requests.get(MatchURL, verify=False)
    MatchLDT = response.json()
    if "player_of_match" in MatchLDT and MatchLDT["player_of_match"]["player_name"] != "":
        pom = find_player(MatchLDT["player_of_match"]["player_name"], SquadFull)
        MatchLDT["player_of_match"]["player_name"] = pom[2] if pom is not None else MatchLDT["player_of_match"]["player_name"]
        MatchLDT["player_of_match"]["team_name"] = pom[3] if pom is not None else "NA"
    if "player_of_series" in MatchLDT and MatchLDT["player_of_series"]["player_name"] != "":
        pos = find_player(MatchLDT["player_of_series"]["player_name"], SquadFull)
        MatchLDT["player_of_series"]["player_name"] = pos[2] if pos is not None else MatchLDT["player_of_series"]["player_name"]
        MatchLDT["player_of_series"]["team_name"] = pos[3] if pos is not None else "NA"
    for key, batsman in MatchLDT["now_batting"].items():
        if batsman["name"] != "":
            player = find_player(batsman["name"], SquadFull)
            batsman["name"] = player[2] if player is not None else batsman["name"]
            batsman["team"] = player[3] if player is not None else "NA"
    for key, bowler in MatchLDT["now_bowling"].items():
        if bowler["name"] != "":
            player = find_player(bowler["name"], SquadFull)
            bowler["name"] = player[2] if player is not None else bowler["name"]
            bowler["team"] = player[3] if player is not None else "NA"
    MatchDT2 = []
    MatchDT2.append(num_suffix(int(MatchDT[0][1])) + " Match" if MatchDT[0][1].isdigit() else MatchDT[0][1])
    MatchDT2.append(MatchDT[0][6].split(", ")[1])
    MatchDT2.append(num_suffix(MatchDT[0][2].day) + " " + MatchDT[0][2].strftime("%B %Y"))
    current_date = datetime.now(tz)
    current_date = current_date.replace(tzinfo=None)
    MatchDT = [dict(row._mapping) for row in MatchDT]
    return serialize({'match': match, 'cd': current_date, 'dt1': MatchDT, 'dt2': MatchDT2, 'dt3': MatchLDT, 'tid': teamID, 'dttm': dttm, 'clr': ptclr, 'clr2': clr, 'inn1': Inn1, 'inn2': Inn2, 'fn': full_name})

@main.route('/match-<match>/liveScore')
def liveScore(match):
    source = request.args.get('source', None)
    team = request.args.get('fteam', None)
    return render_template('live.html', match=match, source=source, fteam=team)

def get_scoreCard(match):
    MatchDT = db.session.execute(text('SELECT * FROM Fixture WHERE "Match_No" = :matchno'), {'matchno': match}).fetchall()
    SquadFull = (db.session.execute(text('SELECT * FROM Squad')).fetchall())
    MatchURL = render_live_URL(MatchDT[0][4], MatchDT[0][5], match, MatchDT[0][2])
    dttm = concat_DT(MatchDT[0][2], MatchDT[0][3])
    response = requests.get(MatchURL, verify=False)
    MatchLDT = response.json()
    if "player_of_match" in MatchLDT and MatchLDT["player_of_match"]["player_name"] != "":
        pom = find_player(MatchLDT["player_of_match"]["player_name"], SquadFull)
        MatchLDT["player_of_match"]["player_name"] = pom[2] if pom is not None else MatchLDT["player_of_match"]["player_name"]
        MatchLDT["player_of_match"]["team_name"] = pom[3] if pom is not None else "NA"
    if "player_of_series" in MatchLDT and MatchLDT["player_of_series"]["player_name"] != "":
        pos = find_player(MatchLDT["player_of_series"]["player_name"], SquadFull)
        MatchLDT["player_of_series"]["player_name"] = pos[2] if pos is not None else MatchLDT["player_of_series"]["player_name"]
        MatchLDT["player_of_series"]["team_name"] = pos[3] if pos is not None else "NA"
    for inn in MatchLDT.get("innings", [])[:2]:
        if "batting" in inn:
            for batsman in inn["batting"]:
                player = find_player(batsman["name"], SquadFull)
                batsman["name"] = player[2] if player is not None else batsman["name"]
                batsman["team"] = player[3] if player is not None else "NA"
        if "bowling" in inn:
            for bowler in inn["bowling"]:
                player = find_player(bowler["name"], SquadFull)
                bowler["name"] = player[2] if player is not None else bowler["name"]
                bowler["team"] = player[3] if player is not None else "NA"
        if "not_batted" in inn:
            nb = sorted(inn['not_batted'].values(), key=lambda x: x['order'])
            for nbb in nb:
                nbd = find_player(nbb["name"], SquadFull)
                nbb["name"] = nbd[2] if nbd is not None else nbb["name"]
                nbb["team"] = nbd[3] if nbd is not None else "NA"
            inn['not_batted'] = nb
        if inn["fall_of_wickets"] is not None:
            fow = []
            if inn["fall_of_wickets"] != "":
                for bt in inn["fall_of_wickets"].split('),'):
                    btd = find_player(bt.split(' (')[1].split(',')[0], SquadFull)
                    n = btd[2] if btd is not None else bt.split(' (')[1].split(',')[0]
                    t = btd[3] if btd is not None else "NA"
                    score = bt.split(' (')[0]
                    over = bt.split(' (')[1].split(', ')[1].strip('()')
                    fow.append({"name": n, "team": t, "score": score, "over": over})
            inn["fall_of_wickets"] = fow
    MatchDT2 = []
    MatchDT2.append(num_suffix(int(MatchDT[0][1])) + " Match" if MatchDT[0][1].isdigit() else MatchDT[0][1])
    MatchDT2.append(MatchDT[0][6].split(", ")[1])
    MatchDT2.append(num_suffix(MatchDT[0][2].day) + " " + MatchDT[0][2].strftime("%B %Y"))
    current_date = datetime.now(tz)
    current_date = current_date.replace(tzinfo=None)
    MatchDT = [dict(row._mapping) for row in MatchDT]
    return serialize({'match': match, 'cd': current_date, 'dt1': MatchDT, 'dt2': MatchDT2, 'dt3': MatchLDT, 'tid': teamID, 'dttm': dttm, 'clr2': clr, 'fn': full_name})

@main.route('/match-<match>/scoreCard')
def scoreCard(match):
    source = request.args.get('source', None)
    team = request.args.get('fteam', None)
    return render_template('scorecard.html', match=match, source=source, fteam=team)

def get_liveSquad(match):
    MatchDT = db.session.execute(text('SELECT * FROM Fixture WHERE "Match_No" = :matchno'), {'matchno': match}).fetchall()
    SquadFull = (db.session.execute(text('SELECT * FROM Squad')).fetchall())
    SquadDT = db.session.execute(text('SELECT * FROM Squad WHERE "Captain" = :captain'), {'captain': 'Y'}).fetchall()
    MatchURL = render_live_URL(MatchDT[0][4], MatchDT[0][5], match, MatchDT[0][2])
    dttm = concat_DT(MatchDT[0][2], MatchDT[0][3])
    response = requests.get(MatchURL, verify=False)
    MatchLDT = response.json()
    for sqd in MatchLDT.get("squad", []):
        if sqd['players'] is not None:
            for player in sqd['players']:
                p = find_player(player['name'], SquadFull)
                player['name'] = p[2] if p is not None else player['name']
                player['team'] = p[3] if p is not None else "NA"
                player['captain'] = (True if p[4] == 'Y' else False) if p is not None else False
        if sqd['substitute_players'] is not None:
            for sub in sqd['substitute_players']:
                p = find_player(sub['name'], SquadFull)
                sub['name'] = p[2] if p is not None else sub['name']
                sub['team'] = p[3] if p is not None else "NA"
                sub['captain'] = (True if p[4] == 'Y' else False) if p is not None else False
        if sqd['bench_players'] is not None:
            for bench in sqd['bench_players']:
                p = find_player(bench['name'], SquadFull)
                bench['name'] = p[2] if p is not None else bench['name']
                bench['team'] = p[3] if p is not None else "NA"
                bench['captain'] = (True if p[4] == 'Y' else False) if p is not None else False
    MatchDT2 = []
    MatchDT2.append(num_suffix(int(MatchDT[0][1])) + " Match" if MatchDT[0][1].isdigit() else MatchDT[0][1])
    MatchDT2.append(MatchDT[0][6].split(", ")[1])
    MatchDT2.append(num_suffix(MatchDT[0][2].day) + " " + MatchDT[0][2].strftime("%B %Y"))
    current_date = datetime.now(tz)
    current_date = current_date.replace(tzinfo=None)
    MatchDT = [dict(row._mapping) for row in MatchDT]
    SquadDT = [dict(row._mapping) for row in SquadDT]
    return serialize({'match': match, 'cd':current_date, 'dt1':MatchDT, 'dt2':MatchDT2, 'dt3':MatchLDT, 'tid':teamID, 'dttm':dttm, 'sqd':SquadDT})

@main.route('/match-<match>/liveSquad')
def liveSquad(match):
    source = request.args.get('source', None)
    team = request.args.get('fteam', None)
    return render_template('livesquad.html', match=match, source=source, fteam=team)

@main.route('/match-<match>/FRScore')
def FRScore(match):
    MatchFR = db.session.execute(text('SELECT * FROM Fixture WHERE "Match_No" = :matchno'),
                                 {'matchno': match}).fetchall()
    MatchFR = MatchFR[0]
    matchDT = datetime.combine(MatchFR.Date, MatchFR.Time)
    current_date = datetime.now(tz)
    current_date = current_date.replace(tzinfo=None)
    source = request.args.get('source', None)
    team = request.args.get('fteam', None)
    if current_date < (matchDT - timedelta(minutes=30)):
        return redirect(url_for('main.matchInfo', match=match, source=source, fteam=team))
    elif current_date >= (matchDT - timedelta(minutes=30)) and MatchFR[10] is None:
        return redirect(url_for('main.liveScore', match=match, source=source, fteam=team))
    elif MatchFR[10] is not None:
        return redirect(url_for('main.scoreCard', match=match, source=source, fteam=team))

@main.route('/todayMatch')
def todayMatch():
    current_date = datetime.now(tz).replace(tzinfo=None).date()
    TodayFR = db.session.execute(text('SELECT * FROM Fixture WHERE "Date" = :current_date order by id'),{'current_date': current_date}).fetchall()
    if len(TodayFR) == 0:
        return render_template('no_live_match.html')
    else:
        dt = [['Match No', 'Date', 'Venue', 'Team-A', 'Team-B', 'TA-Score', 'TB-Score', 'WT', 'WType', 'WBy', 'Result']]
        for i in TodayFR:
            dtt = []
            dtt.append(i[1])  # Match No
            dttm = i[2].strftime('%Y-%m-%d') + ' ' + \
                   i[3].strftime('%H:%M:%S')
            dtt.append(datetime.strptime(dttm, '%Y-%m-%d %H:%M:%S'))  # DateTime
            dtt.append(i[6].split(', ')[1])  # Venue
            dtt.append(i[4])  # Team A
            dtt.append(i[5])  # Team B
            A, B = i[8], i[9]
            dtt.append(A)  # TA_Scr
            dtt.append(B)  # TB_Scr
            if i[10] is None:
                dtt.append('TBA')  # Win-Team
                dtt.append('TBA')
                dtt.append('TBA')
            elif i[10] == 'NA':
                dtt.append('NA')
                dtt.append('NA')
                dtt.append('NA')
                dtt.append(i[7])
            else:
                dtt.append(i[10])
                WType = 'wickets' if 'wickets' in i[7] else 'runs'
                dtt.append(WType)
                WBy = re.findall(r'\d+', i[7])[0]
                dtt.append(str(WBy))
                dtt.append(i[7][i[7].index('won'):])
            dt.append(dtt)
        current_date = datetime.now(tz)
        current_date = current_date.replace(tzinfo=None)
        return render_template('liveMatches.html', FR=dt, fn=full_name, current_date=current_date, clr=clr)

@main.route('/update')
@login_required
def update():
    FR = Fixture.query.all()
    if request.args.get('key'):
        key = request.args.get('key')
    else:
        key = None
    return render_template('update.html', key=key, FR=FR)

@main.route('/updatematch', methods=['POST'])
@login_required
def updatematch():
    hint = request.form.get('hint')
    key = 1
    if request.method == "POST" and hint == 'before':
        match = str(request.form.get('match')).upper()
        match = int(match) if match.isdigit() else pofs[match]
        FR = Fixture.query.filter_by(Match_No=str(match)).first()
        if match not in [i for i in range(1, 71)]+list(pofs.values()):
            flash('Invalid Match number to update', category='error')
            return redirect(url_for('main.update', key=key))
        if FR.Win_T != None:
            flash('Result for Match {} already updated, delete to update it again'.format(match), category='warning')
            return redirect(url_for('main.update', key=key))
        if FR.Team_A == 'TBA' or FR.Team_B == 'TBA':
            flash('Teams are not updated for Playoff Match {} to update its result'.format(match), category='warning')
            return redirect(url_for('main.update', key=key))
        return render_template('updatematch.html', FR=FR, fn=full_name, match=match)
    if request.method == 'POST' and hint == 'after':
        A = [int(request.form['runsA']), float(request.form['oversA']), int(request.form['wktsA'])]
        B = [int(request.form['runsB']), float(request.form['oversB']), int(request.form['wktsB'])]
        wt, win_type, win_by = str(request.form['wt']).upper(), str(request.form['win_type']), str(request.form['win_by'])
        result = '{} won by {} {}'.format(full_name[wt], win_by, win_type)
        match_no = request.form['match']
        FR = Fixture.query.filter_by(Match_No=str(match_no)).first()
        a, b = FR.Team_A,  FR.Team_B
        FR.Result = result
        FR.Win_T = wt
        FR.A_info, FR.B_info = {'runs':A[0], 'overs':A[1], 'wkts':A[2]}, {'runs':B[0], 'overs':B[1], 'wkts':B[2]}
        db.session.commit()
        if match_no.isdigit():
            A[1] = 50 if A[2] == 10 else A[1]
            B[1] = 50 if B[2] == 10 else B[1]
            dataA = db.session.execute(text('SELECT team_name, "P", "W", "L", "Points", "For", "Against", "Win_List" FROM pointstable WHERE team_name = :team_name'),{'team_name': str(a)}).fetchall()
            for i in dataA:
                if i[0] == wt:
                    P, W, L, Points = 1 + i[1], 1 + i[2], 0 + i[3], i[4] + 2
                    wl = eval(i[7])
                    wl[int(match_no)] = 'W'
                    wl = dict(sorted(wl.items()))
                else:
                    P, W, L, Points = 1 + i[1], 0 + i[2], 1 + i[3], i[4] + 0
                    wl = eval(i[7])
                    wl[int(match_no)] = 'L'
                    wl = dict(sorted(wl.items()))
                forRuns = i[5]['runs'] + A[0]
                forOvers = oversAdd(i[5]['overs'], A[1])
                againstRuns = i[6]['runs'] + B[0]
                againstOvers = oversAdd(i[6]['overs'], B[1])
                NRR = round((forRuns / ovToPer(forOvers) - againstRuns / ovToPer(againstOvers)), 3)
            PT = Pointstable.query.filter_by(team_name=str(a)).first()
            PT.P, PT.W, PT.L, PT.Points, PT.NRR, PT.Win_List = P, W, L, Points, NRR, str(wl)
            PT.For = {"runs": forRuns, "overs": forOvers}
            PT.Against = {"runs": againstRuns, "overs": againstOvers}
            db.session.commit()

            dataB = db.session.execute(text('SELECT team_name, "P", "W", "L", "Points", "For", "Against", "Win_List" FROM pointstable WHERE team_name = :team_name'),{'team_name': str(b)}).fetchall()
            for i in dataB:
                if i[0] == wt:
                    P, W, L, Points = 1 + i[1], 1 + i[2], 0 + i[3], i[4] + 2
                    wl = eval(i[7])
                    wl[int(match_no)] = 'W'
                    wl = dict(sorted(wl.items()))
                else:
                    P, W, L, Points = 1 + i[1], 0 + i[2], 1 + i[3], i[4] + 0
                    wl = eval(i[7])
                    wl[int(match_no)] = 'L'
                    wl = dict(sorted(wl.items()))
                forRuns = i[5]['runs'] + B[0]
                forOvers = oversAdd(i[5]['overs'], B[1])
                againstRuns = i[6]['runs'] + A[0]
                againstOvers = oversAdd(i[6]['overs'], A[1])
                NRR = round((forRuns / ovToPer(forOvers) - againstRuns / ovToPer(againstOvers)), 3)
            PT = Pointstable.query.filter_by(team_name=str(b)).first()
            PT.P, PT.W, PT.L, PT.Points, PT.NRR, PT.Win_List = P, W, L, Points, NRR, str(wl)
            PT.For = {"runs": forRuns, "overs": forOvers}
            PT.Against = {"runs": againstRuns, "overs": againstOvers}
            db.session.commit()
        flash('Match {} result updated successfully'.format(match_no), category='success')
        return redirect(url_for('main.update', key=key))

@main.route('/deletematch', methods=['POST'])
@login_required
def deletematch():
    hint = request.form.get('hint')
    key = 2
    if request.method == "POST" and hint == 'before':
        dmatch = str(request.form.get('dmatch')).upper()
        dmatch = int(dmatch) if dmatch.isdigit() else pofs[dmatch]
        FR = Fixture.query.filter_by(Match_No=str(dmatch)).first()
        if dmatch not in [i for i in range(1, 71)] + list(pofs.values()):
            flash('Invalid Match number to delete', category='error')
            return redirect(url_for('main.update', key=key))
        if FR.Win_T == None:
            flash('Result for Match {} is not yet updated to delete'.format(dmatch), category='warning')
            return redirect(url_for('main.update', key=key))
        return render_template('deletematch.html', FR=FR, fn=full_name, dmatch=dmatch)
    if request.method == "POST" and hint == 'after':
        dmatch = request.form.get('dmatch')
        if dmatch.isdigit():
            FR = db.session.execute(text('SELECT "Team_A", "Team_B", "A_info", "B_info", "Win_T" FROM fixture WHERE "Match_No" = :match_no'),{'match_no': dmatch}).fetchall()
            for i in FR:
                A = list(i[2].values())
                B = list(i[3].values())
                #A = [int(A[0]), float(A[1]), int(A[2])]
                #B = [int(B[0]), float(B[1]), int(B[2])]
                wt = i[4]
                a, b = i[0], i[1]
            A[1] = 50 if A[2] == 10 else A[1]
            B[1] = 50 if B[2] == 10 else B[1]

            dataA = db.session.execute(text('SELECT team_name, "P", "W", "L", "Points", "For", "Against", "Win_List" FROM pointstable WHERE team_name = :team_name'),{'team_name': str(a)}).fetchall()

            for i in dataA:
                if i[0] == wt:
                    P, W, L, Points = i[1] - 1, i[2] - 1, i[3] - 0, i[4] - 2
                    wl = eval(i[7])
                    del wl[int(dmatch)]
                    wl = dict(sorted(wl.items()))
                else:
                    P, W, L, Points = i[1] - 1, i[2] - 0, i[3] - 1, i[4] - 0
                    wl = eval(i[7])
                    del wl[int(dmatch)]
                    wl = dict(sorted(wl.items()))
                forRuns = i[5]['runs'] - A[0]
                forOvers = oversSub(i[5]['overs'], A[1])
                againstRuns = i[6]['runs'] - B[0]
                againstOvers = oversSub(i[6]['overs'], B[1])
                if ovToPer(forOvers) == 0 or ovToPer(againstOvers) == 0:
                    NRR = 0.0
                else:
                    NRR = round((forRuns / ovToPer(forOvers) - againstRuns / ovToPer(againstOvers)), 3)
            PT = Pointstable.query.filter_by(team_name=str(a)).first()
            PT.P, PT.W, PT.L, PT.Points, PT.NRR, PT.Win_List = P, W, L, Points, NRR, str(wl)
            PT.For = {"runs": forRuns, "overs": forOvers}
            PT.Against = {"runs": againstRuns, "overs": againstOvers}
            db.session.commit()


            dataB = db.session.execute(text('SELECT team_name, "P", "W", "L", "Points", "For", "Against", "Win_List" FROM pointstable WHERE team_name = :team_name'),{'team_name': str(b)}).fetchall()
            for i in dataB:
                if i[0] == wt:
                    P, W, L, Points = i[1] - 1, i[2] - 1, i[3] - 0, i[4] - 2
                    wl = eval(i[7])
                    del wl[int(dmatch)]
                    wl = dict(sorted(wl.items()))
                else:
                    P, W, L, Points = i[1] - 1, i[2] - 0, i[3] - 1, i[4] - 0
                    wl = eval(i[7])
                    del wl[int(dmatch)]
                    wl = dict(sorted(wl.items()))
                forRuns = i[5]['runs'] - B[0]
                forOvers = oversSub(i[5]['overs'], B[1])
                againstRuns = i[6]['runs'] - A[0]
                againstOvers = oversSub(i[6]['overs'], A[1])
                if ovToPer(forOvers) == 0 or ovToPer(againstOvers) == 0:
                    NRR = 0.0
                else:
                    NRR = round((forRuns / ovToPer(forOvers) - againstRuns / ovToPer(againstOvers)), 3)
            PT = Pointstable.query.filter_by(team_name=str(b)).first()
            PT.P, PT.W, PT.L, PT.Points, PT.NRR, PT.Win_List = P, W, L, Points, NRR, str(wl)
            PT.For = {"runs": forRuns, "overs": forOvers}
            PT.Against = {"runs": againstRuns, "overs": againstOvers}
            db.session.commit()

        FR = Fixture.query.filter_by(Match_No=dmatch).first()
        FR.Result = None
        FR.Win_T = None
        FR.A_info, FR.B_info = {'runs': 0, 'overs': 0.0, 'wkts': 0}, {'runs': 0, 'overs': 0.0, 'wkts': 0}
        db.session.commit()
        flash('Match {} result deleted successfully'.format(dmatch), category='success')
        return redirect(url_for('main.update', key=key))

@main.route('/updateplayoffs', methods=['POST'])
@login_required
def updateplayoffs():
    hint = request.form.get('hint')
    key = 3
    if request.method == "POST" and hint == 'before':
        pomatch = request.form.get('pomatch').upper()
        if pomatch not in [str(i) for i in range(1, 71)] + ['SF1', 'SF2', 'F']:
            flash('Invalid match, Select a valid Playoff Match', category='error')
            return redirect(url_for('main.update', key=key))
        FR = Fixture.query.filter_by(Match_No=pofs[pomatch]).first()
        return render_template('playoffsupdate.html', pomatch=pofs[pomatch], teams=full_name, FR=FR)
    if request.method == 'POST' and hint == 'after':
        pomatch = request.form.get('pomatch')
        FR = Fixture.query.filter_by(Match_No=pomatch).first()
        if request.form.get('checkA') == 'YES':
            FR.Team_A = request.form.get('teamA')
        if request.form.get('checkB') == 'YES':
            FR.Team_B = request.form.get('teamB')
        if request.form.get('checkV') == 'YES':
            FR.Venue = request.form.get('venue')
        db.session.commit()
        flash('{} Playoff teams updated successfully'.format(pomatch), category='success')
        return redirect(url_for('main.update', key=key))

@main.route('/updatequalification', methods=['POST'])
@login_required
def updatequalification():
    key = 4
    qteam = request.form.get('qteam')
    PT = Pointstable.query.filter_by(team_name=qteam).first()
    PT.qed = "Q"
    db.session.commit()
    flash('Updated Qualification status for {} successfully'.format(qteam), category='success')
    return redirect(url_for('main.update', key=key))

@main.route('/updateelimination', methods=['POST'])
@login_required
def updateelimination():
    key = 5
    eteam = request.form.get('eteam')
    PT = Pointstable.query.filter_by(team_name=eteam).first()
    PT.qed = "E"
    db.session.commit()
    flash('Updated Elimination status for {} successfully'.format(eteam), category='success')
    return redirect(url_for('main.update', key=key))
