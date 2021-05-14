from hdrh import histogram
import sys, traceback
import argparse
import logging
from datetime import datetime
import connection
import psycopg2 as pg
import csv
import jellyfish as jf
#pip install jaccard-index 
#from similarity.jaccard import Jaccard as ja


#SIM_METHODS=[jf.levenshtein_distance, jf.jaro_distance, jf.damerau_levenshtein_distance, jf.metaphone, jf.soundex, jf.nysiis, jf.match_rating_codex]

#logging
logger= logging.getLogger('sfinspect')
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
logger.addHandler(ch)

#TABLE NAMES TO USE
RAW_INSPECTION_TBL ="rawinspection"
CLEAN_REST_TBL="cleanrest"
CLEAN_INSPECTION_TBL="cleaninspection"
JOINED_BIKE_INSPECTION_TBL="joinedinspbike"
SF_INSPECTION_TBL = 'sfinspection'
SF_INSPECTION_TEMP =  SF_INSPECTION_TBL + '_temp'

REST_CLUSTER_TBL = 'rest_with_cluster_id'

TABLES_IN_SCHEMA = [SF_INSPECTION_TBL, CLEAN_INSPECTION_TBL, CLEAN_REST_TBL, JOINED_BIKE_INSPECTION_TBL]

#COLUMN NAMES TO USE
BUSINESS_COL = ['business_name', 'business_address', 'business_city', 'business_state', 'business_postal_code', 'business_latitude', 'business_longitude', 'business_phone_number']
INSPECTION_COL = ['business_name', 'business_address', 'inspection_id', 'inspection_date', 'inspection_score', 'inspection_type', 'violation_id', 'violation_description', 'risk_category']
DIRTY_COL = ['business_name', 'business_address', 'business_city', 'business_state', 'business_postal_code', 'business_latitude', 'business_longitude', 'business_phone_number', 'inspection_id', 'inspection_date', 'inspection_score', 'inspection_type', 'violation_id', 'violation_description', 'risk_category']

BUSINESS_ID = 'business_id'
BUSINESS_ID_MATCH =  BUSINESS_ID + '_match'

#THRESHOLD TO USE
THRESHOLD = 0.7

class client:
    def __init__(self):
        self.dbname=connection.dbname
        self.dbhost=connection.dbhost
        self.dbport=connection.dbport
        self.dbusername=connection.dbusername
        self.dbpasswd=connection.dbpasswd

        self.conn=None #Connection to DBs

    # open a connection to a psql database, using the self.dbXX parameters
    def open_connection(self):
        logger.debug("Opening a Connection")
        self.conn = pg.connect(dbname=self.dbname, user=self.dbusername)
        return True

    # Close any active connection(handle closing a closed conn)
    def close_connection(self):
        logger.debug("Closing Connection")
        #Avoiding trying to close a closed connection
        if(self.conn.closed==0):
          self.conn.close()

        return True

    #Reference:
    #https://stackoverflow.com/questions/19472922/reading-external-sql-script-in-python
    def execute_scripts_from_file(self, filename):
        
        cur = self.conn.cursor()

        # Open and read the file as a single buffer
        fd = open(filename, 'r')
        sqlFile = fd.read()
        fd.close()

        # all SQL commands (split on ';')
        sqlCommands = sqlFile.split(';')

        # Execute every command from the input file
        for command in sqlCommands:
            try:
                cur.execute(command)
                self.conn.commit()
            except Exception as e:
                # print ('Failed to run command!+command+'. '+str(e))

                #Rollback previous aborted transaction
                self.conn.rollback()

        cur.close()



    # Load the inspection data via TSV loading or via hbase. Use happybase as
    # the library for hbase (hbase == hbase)
    def load_inspection(self, limit_load=None, load_file=None):
        logger.debug("Loading Inspection")
        if load_file == None:
            raise Exception("No Load Details Provided")

        cur = self.conn.cursor()

        f = open(load_file, 'r')
        reader = csv.reader(f, delimiter='\t')
        next(reader) # Skip the header row.

        #Erase table if has elements
        cur.execute("DELETE FROM {}".format(SF_INSPECTION_TBL))    

        #Copy from csv to table
        copy_command = """
           COPY sfinspection FROM stdin WITH CSV HEADER
           DELIMITER as '\t'
           """
        cur.copy_expert(sql=copy_command, file=f)

        self.conn.commit()
        cur.close()


        #Create a new table with a copy of all inspections rows, plus 
        #additional columns for blocking and later matching
        self.create_temp(SF_INSPECTION_TBL, SF_INSPECTION_TEMP, \
            id_name=BUSINESS_ID, other_cols=['cluster_id',BUSINESS_ID_MATCH,
                                             'matched', 'similarity_score'])

        return

    #.
    def find_cands(self):
        logger.debug("Find candidates")
        '''
        Implements a blocking solution that finds a set of possible "promising 
        matches". Use one record-level blocking, and one string blocking strategy
        Elements in the same cluster have same postal code and same 3 first 
        characters in their names
        '''

        #Counter of clusters. User for creating cluster_ids
        cluster_counter=1

        #Get all possible zipcodes

        cur = self.conn.cursor()
        cur.execute("""SELECT DISTINCT business_postal_code FROM {};""".\
            format(SF_INSPECTION_TEMP))
        zipcodes=cur.fetchall()

        for zipcode in zipcodes:
            zipcode = zipcode[0]# if zipcode[0]!='' else None 
            
            #String level blocking. All business with same 3 char in name
            cur.execute("""SELECT DISTINCT LEFT(business_name, 3) FROM {} \
                WHERE business_postal_code = %s;""".\
                format(SF_INSPECTION_TEMP),(zipcode,))

            all_possible_first_three_chars_for_names=cur.fetchall()
            
            for two_chars in all_possible_first_three_chars_for_names:
    
                #Updating table, creating cluset ids
                #For the moment they are all set with 1, have to remove that later

                cur.execute("""UPDATE {} SET cluster_id = %s WHERE \
                    business_postal_code = %s AND LEFT(business_name, 3) = %s;""".\
                    format(SF_INSPECTION_TEMP), (cluster_counter, zipcode,two_chars))
                
                self.conn.commit()

                cluster_counter+=1



    # join all with all (this function might be replace in compute_similarity 
    #by find_cands when implemented)
    def create_temp(self, from_table, to_table, columns_lst=None, id_name=None, \
                    other_cols=None):
        logger.debug("Create dirty temporal table")

        cur = self.conn.cursor()

        cur.execute("""DROP TABLE IF EXISTS {};""".format(to_table))
        cur.execute("""
            CREATE TABLE {} AS TABLE {} WITH NO DATA;""".format(to_table, from_table))
 
        if id_name:       
            cur.execute("""
                ALTER TABLE {} ADD COLUMN {} INT PRIMARY KEY GENERATED BY DEFAULT \
                AS IDENTITY;""".format(to_table, id_name))
        
        if other_cols: 
            for col in other_cols:
                cur.execute("""
                    ALTER TABLE {} ADD COLUMN {} REAL;""".format(to_table, col))
        
        if columns_lst:
            cur.execute("""
                INSERT INTO {} SELECT {} FROM {} ;""".format(to_table,','.\
                                            join(columns_lst), from_table))
        else:
            cur.execute("""
                INSERT INTO {} SELECT * FROM {} ;""".format(to_table, from_table))

        self.conn.commit()
        cur.close()
         
    def jaccard_similarity(self, x,y,k):
         #Used from: https://dataconomy.com/2015/04/implementing-the-five-most-\
         #popular-similarity-measures-in-python/
        set1 = set()
        for i in range(len(x)-k):
            set1.add(x[i:i+k])
        set2 = set()
        for i in range(len(y)-k):
            set2.add(y[i:i+k])

        intersection_cardinality = len(set.intersection(*[set1, set2]))
        union_cardinality = len(set.union(*[set1, set2]))
        return intersection_cardinality/float(union_cardinality)


    def combine_similarity_scores(self, str1a, str2a, str3a, str1b, str2b, str3b):
        score1 = self.jaccard_similarity(str1a, str1b, 2)
        score2 = jf.jaro_distance(str2a, str2b)
        score3 = jf.levenshtein_distance(str3a, str3b)
        score = score1*0.40 + score2*0.55 + score3*0.05
        return score

    #def calculate_matching_id():


    # computes string similarity for business name and two additional fields \
    #for every pair of records in the candidate set
    def compute_similarity(self):
        logger.debug("Compute similarity")
      
        cur = self.conn.cursor()

        cur.execute("""SELECT MAX(cluster_id) FROM {};""".format(SF_INSPECTION_TEMP))
        n_clusters=int(cur.fetchone()[0])
        
        for cluster in range(1,n_clusters+1):
            
            # print("cluster_id")
            # print(cluster)

            #Cross join between all different tuples inside the cluster
            cur.execute("""
                WITH sfinspection_temp_cl AS (SELECT * 
                                         FROM  sfinspection_temp 
                                         WHERE cluster_id = %s) 
                SELECT  a.cluster_id,
                        a.business_id,
                        a.business_name,
                        a.business_address,
                        a.business_city,
                        b.business_name,
                        b.business_address,
                        b.business_city, 
                        b.business_id
                        FROM sfinspection_temp_cl as a 
                        CROSS JOIN sfinspection_temp_cl as b
                        WHERE a.business_id != b.business_id
                        ORDER BY a.business_id;""",(cluster,))

            possible_matches = cur.fetchall()

            for pair in possible_matches:

                matched=0               
                bus_id = pair[1]

                score = self.combine_similarity_scores(pair[2], pair[3], \
                                    pair[4], pair[5], pair[6], pair[7])

                matched = 1
                cur.execute("""
                    UPDATE {} SET business_id_match = %s, matched = %s,  \
                    similarity_score= %s  WHERE business_id = %s;""".\
                    format(SF_INSPECTION_TEMP),(pair[0], matched, score, bus_id))

                self.conn.commit()

        self.conn.commit()
        cur.close()

   
    def get_longest_element_of_array(self, lst):
        '''
        Takes an list, compares the len of each element and return the longest
        Input:
            lst: a list of strings to compare
        Output:
            the longest string in the list
        '''
        longest_element=''
        for element in lst:
            if(len(str(element))>len(str(longest_element))):
                longest_element=element
        return longest_element 

    def get_authoritative_business(self,cluster_id):

        cur = self.conn.cursor()

        authoritative_element={}
        authoritative_element['cluster_id']=cluster_id

        for index, element in enumerate(BUSINESS_COL):                

            # Get info of the ones that matched
            cur.execute("""SELECT {} FROM {} WHERE cluster_id = %s AND matched=1;""".\
                format(element,SF_INSPECTION_TEMP), (cluster_id,) )
            
            #Results of query, just purer
            elements=[el[0] for el in cur.fetchall()]

            #If there are no matches in the cluster, just return null
            if(len(elements)==0):
                return None
            
            #Get best element (longest string)
            longest = self.get_longest_element_of_array(elements)
            
            #Set this atribute in the autoritative element
            authoritative_element[element]=longest

        cur.close()
        return authoritative_element


    def create_rest_with_cluster_id_table(self):

        cur = self.conn.cursor()

        cur.execute("""DROP TABLE IF EXISTS {};""".format(REST_CLUSTER_TBL))

        cur.execute("""
            CREATE TABLE rest_with_cluster_id (
            cluster_id int,
            business_name varchar(100),
            business_address varchar(100),   
            primary key (cluster_id));""")

        cur.close()
        self.conn.commit()

        return

    def populate_clean_insepctions(self):
        '''
        Insert into pure inspections
        For inspections whose restaurants where matched with other restaurants,
        we will write the name and address of the authoritative representative 
        '''

        cur = self.conn.cursor()


        cur.execute(""" 
            INSERT INTO {} 
            SELECT 

            (CASE WHEN i.matched=1 then res_clu.business_name else i.business_name end) as business_name,
            (CASE WHEN i.matched=1 then res_clu.business_address else i.business_address end) as business_address,

            i.inspection_id,
            i.inspection_date,
            i.inspection_score,
            i.inspection_type,
            i.violation_id,
            i.violation_description,
            i.risk_category

            FROM {} as i 
            LEFT JOIN {} as res_clu
            ON i.cluster_id = res_clu.cluster_id
            WHERE i.business_postal_code is not null
            ;""".format(CLEAN_INSPECTION_TBL, SF_INSPECTION_TEMP, REST_CLUSTER_TBL))


        self.conn.commit()


    def update_matches(self, businesses):
        '''
        Insert business into cleanrest
        '''
        cur = self.conn.cursor()

        #Empty tables in case they had previous data
        cur.execute("""DELETE FROM {};""".format(CLEAN_INSPECTION_TBL))
        cur.execute("""DELETE FROM {};""".format(CLEAN_REST_TBL))

        for b in businesses:
            
            #Create tuple ready to be inserted
            b_info=list(b[el] for el in BUSINESS_COL)
        
            #Insert into pure rest table
            cur.execute("""INSERT INTO {} VALUES (%s,%s,%s,%s,%s,%s,%s,%s);""".\
                format(CLEAN_REST_TBL), (b_info))
            self.conn.commit()

        self.populate_clean_insepctions()
        

    def get_business_that_did_not_match(self, cluster_id):
        
        cur = self.conn.cursor()

        # Get info of the business that did not matched
        cur.execute("""SELECT * FROM {} WHERE cluster_id = %s AND matched is null;""".\
            format(SF_INSPECTION_TEMP), (cluster_id,) )
        
        businesses= cur.fetchall()

        #If no elements, return None
        if len(businesses)==0:
            return None
        
        #Prepare list of business to return
        businesses_that_did_not_match=[]

        #Transform each row into a dictionary, and append it to the list
        for business in businesses:
            business_in_dict_form ={}
            business_in_dict_form['cluster_id']=cluster_id

            for index, element in enumerate(BUSINESS_COL):
                business_in_dict_form[element]=business[index]

            businesses_that_did_not_match.append(business_in_dict_form)

        cur.close()
        return businesses_that_did_not_match 


    # Clean restaurants table to have a single correct restaurant entry for all likely variations
    def clean_dirty_inspection(self):
        
        logger.debug("Cleaning Dirty Data")

        self.find_cands()

        self.compute_similarity()

        #Get number of clusters 
        cur = self.conn.cursor()
        cur.execute("""SELECT MAX(cluster_id) FROM {};""".format(SF_INSPECTION_TEMP))
        n_clusters=int(cur.fetchone()[0])

        #For each cluster
        business=[]
        # names_and_address_set = set()

        #We first look over all clusters and scollect business that have matches
        for cluster_id in range(1,n_clusters+1):

            #Get one authoritative business for cluster
            authoritative_element = self.get_authoritative_business(cluster_id)
            if(authoritative_element):

                #Change this to a sql insertion if have time
                business.append(authoritative_element)

                #Save the connection between the autoritative_business and the cluster
                clusterid_name_address_bussiness = [authoritative_element[element] \
                    for element in ['cluster_id', 'business_name','business_address']]

                cur.execute("""INSERT INTO {} VALUES (%s,%s,%s);""".\
                    format(REST_CLUSTER_TBL), (clusterid_name_address_bussiness))

                self.conn.commit()

        #We know go over all clusters again looking for those without matches
        #We do this in two loops because we want to be sure to insert all matches 
        #across clusters first 
        #print(names_and_address_set)

        for cluster_id in range(1,n_clusters+1):
            #Add business that did not matched to the business list
            bs_that_did_not_match = self.get_business_that_did_not_match(cluster_id)
            if(bs_that_did_not_match):
                for b_not_matched in bs_that_did_not_match:
                    
                    #First check if the (name,address) was not already detected 
                    #in one of the matched businesses. For example:
                    #SUNSET SUPERMARKET | 2425 IRVING St. is in clusters 1071 
                    #and 1091 because it appears several times with postal code 
                    #94122, and one time with 24192 (clearly a typo). The last one 
                    #will appear like a new business because it is in a different 
                    #cluster (cause different postal code), but because it has 
                    #the exact name and address, we cannot insert it to cleanrest. 
                    #Hence, before inserting it to clearest, we have to check if 
                    #we have already found it. We check that in the REST_CLUSTER_TBL 
                    #which contains the authoritative names.

                    cur.execute("""
                        SELECT * FROM  {} 
                        WHERE business_name=%s and business_address=%s;""".\
                        format(REST_CLUSTER_TBL), (b_not_matched['business_name'],\
                            b_not_matched['business_address']))

                    already_in_table = cur.fetchone()

                    if(not already_in_table):
                        business.append(b_not_matched)
                        
        self.update_matches(business)

        return

    # create tables
    def build_tables(self):

        #Drop all tables if they exist first
        cur=self.conn.cursor()
        for table in TABLES_IN_SCHEMA:
            cur.execute("""DROP TABLE IF EXISTS {};""".format(table))
        
        cur.execute("""DROP TABLE IF EXISTS {};""".format(SF_INSPECTION_TEMP))
        self.conn.commit()


        logger.debug("Building Tables")

        #Buid tables in schema
        self.execute_scripts_from_file('schemas.sql')

        #Create tables that associates cluster ids with authoritative_business
        self.create_rest_with_cluster_id_table()

        return

    
    def build_indexes(self):
        '''
        Create indexes to improve the performance of the queries
        '''
        logger.debug("Building Indexes")

        cur = self.conn.cursor()
        
        #Hash indexes for inspections table
        hash_index_for_sf_inspection_temp = ['cluster_id','matched']
        for index in hash_index_for_sf_inspection_temp:
            cur.execute("""
               CREATE INDEX {} ON {} USING HASH ({});""".\
               format(index+'_id_index', SF_INSPECTION_TEMP, index))

        #B+ tree index for similarity score
        cur.execute("""
           CREATE INDEX {} ON {} ({});""".\
           format('similarity_score_index', SF_INSPECTION_TEMP, 'similarity_score'))

        self.conn.commit()

        return

    
    def join_tables(self):
        '''
        Join cleaned restaurants, inspections, and bike rides
        '''
        logger.debug("Joining Tables")

        cur = self.conn.cursor()

        cur.execute("""DELETE FROM {};""".format(JOINED_BIKE_INSPECTION_TBL))

        self.conn.commit()

        cur.execute("""
           INSERT INTO joinedinspbike (duration,
                           bike_id,
                           violation_id,
                           inspection_date)
           SELECT
               bike.duration,
               bike.bike_id,
               ins.violation_id,
               ins.inspection_date
           FROM cleaninspection as ins
           CROSS JOIN (SELECT
                           trip.end_time_and_date,
                           trip.trip_duration AS duration,
                           trip.bike_id,
                           station.latitude,
                           station.longitude
                           FROM trip LEFT JOIN station
                           ON trip.end_station_id = station.id ) as bike
           WHERE CAST(bike.end_time_and_date AS DATE) BETWEEN ins.inspection_date 
           AND  ins.inspection_date + 7;""")

        self.conn.commit()

        return


    
    def check_tables(self, list_of_sql):
        '''
        Place holder test function
        '''
        logger.debug("Checking Tables")
        
        cur = self.conn.cursor()
        res = []
        for sql in list_of_sql:
            cur.execute(sql)
            for row in cur.fetchall():
                res.append(row)
        return res