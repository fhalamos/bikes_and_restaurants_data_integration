## Description of project

The goal of this project is to clean a 'dirty' dataset (inspections of San Francisco restaurants), and later integrate it with San Francisco bike trip data. The project involves loading data, cleaning data (using string similarity and blocking), uploading data to a postgres database, and joining tables for queries.

Dirty data of the SF inspections are in table `sfinspection`.

Raw inspection table contains denormalized data containing business and inspection information. Moreover, the restaurant data also contains many errors, with the same restaurant being expressed in different names, abbreviations, addresses, etc. We will want all inspections for the same business to be mapped to a single restaurant record.

We transform this into 2 clean tables, as defined in `schemas.sql`:

`cleanrest` contains the authoritative restaurant details
`cleaninspection` will link inspections to the cleaned restaurants.

Lastly, we join the clean data with bike data from a table called `joinedinspbike`.

## How to run

Run `python3 insdriver.py` to run all phases of the data loading, cleaning and integration.

## Description of methods

### Blocking
method `find_cands` implements a blocking solution that finds a set of possible "promising matches." We use one record-level blocking, and one string blocking strategy. For record-level blocking, we implement a strategy that reduces the number of string similarity comparisons you make. We also use cheap string filters to eliminate non-matches.

### Computing Similarity Scores
method `compute_similarity` that computes string similarity for business name and two additional fields for every pair of records identified in the candidate set.

## Integrating the Data

method called `clean_dirty_inspection` in `rest_inspection.py` performs all cleaning and uploading. method `update_matches` populates the `cleanrest` and `cleaninspection` tables.

## Joining Bike Data
method `join_trips` method accomplishes the following: using the provided distance function, finds bike trips ending within one week after a violation at restaurants  (based on the cleaned data) whose location is within 800 meters of the trip destination. We use the results of the query to populate the table `joinedinspbike`

## Design choices

We created a table called `sfinspection_temp` that copied all the information contained in table `sfinspection`, and added columns that we needed for bocking and matching. For example, we created a column called `cluster_id` which indicated to which block/cluster each row was sent to. We also created a column called `matched`, which had a 1 on records that matched with other elements in their clusters, and empty when they did not. We also stored a unique ID for each record, which we called it `bussines_id`.
Another column used in the process of matching the restaurants was the `bussines_id_match` in order to have a unique ID for each group of observations that corresponded to the same restaurant

For the purpose of our blocking, matching, and cleaning we decided to use IDs that uniquely identified each record (weirdly called bussines_id instead of inspection_id) and IDs that uniquely identified the clusters/blocks (called cluster_id). We also created table that linked the cluster ID to the authoritative name and address of each restaurant.

### Blocking
Record-level blocking: group restaurants that have same postal code.
String blocking: group restaurants whose names start with the same 3 characters
After applying these two methods, all elements within the same block have the same postal code and 3 characters in the name.

### Similarity Score
For the similarity matching, we created a function that takes 3 columns from the table sfinspection and compares them to the same columns of another record in the table. The 3 columns that we chose were Name, Address, and City. We acknowledge that the City in this specific application is not an interesting column to use for the matching since all of them are San Francisco, but we considered that for an extension of this exercise on a broader database with different cities this would have been an interesting column to use. Acknowledging this we decided to give the city’s similarity score a very low weight in the aggregated similarity score.
We tried several matching methods and packages. And, for the purpose of practicing, we chose to code ourselves the Jaccard method. This is a set method that gives us the ratio between the shared sets (of k-grams)  of both strings and the total sets in both strings.
We used a edit distance-based method. To be more specific, we used the Levenshtein distance from the package jellyfish. This method computes the different inserts, deletes and substitutes that needs to be done in order to go from one string to the compared one.
Finally, we decided to use the jaro distance algorithm also from the jellyfish package. This was chosen because  it is one of the most popular algorithms for matching since is has a good performance.
In sum, our function uses these three algorithms to calculate in which extension the compared columns of different observations (restaurants in our case) are the same. Those three comparisons are lately used by the function to give a final weighted average that compares those two observations.  The weights for this final aggregation should be decided with some knowledge of the database and the use of it. We based our decision in simple comparisons of the distribution of the separate results.

### Matching Function
Our matching function uses the similarity score function and calculates the score for every tupple within its block. If it is matched at least with one of the other records it will be marked as matched. And all the observations marked as matched within the same cluster will be considered the same restaurant.
The threshold for the similarity_score that we used was 0.7 because we found that there were many scores in the extremes (near the 0 or near the 1). An improved way of defining the threshold would involve creating a function that calculates False positive and False negative in the matching (we would have to label some matches manually for testing this).

### Authoritative representative
To decide which would be the name, address, and city to keep in out clean table of restaurants we decided to use the most complete version of the ways in which each of those columns were written i.e. we kept the longest string in each field.
We consider this is a good decision but can still be improved by taking some considerations about the similarity score that the authoritative have with the other strings. We consider that the best option could have implemented would have been a combination that accounts for the completeness of the record and the higher similarity score that it has, in average, with the all the other records.  

### Clean Restaurants
Our table of clean restaurants includes all the matched restaurants as only one observation with the name and address as we obtained them from the authoritative function. We also included the unmatched restaurants with the same name and address they had in the original table.
The restaurants that have no location (no postal code) were not kept since we considered that an indispensable attribute of the restaurant to clearly identify it in its location/address.

### Clean Inspections
For the creation of this table, we kept all the information related to the inspection details from the original file. Information relative to the location of the inspection (name and address of business) came from the autoritative restaurant in case of the restaurant being part of a cluster, or we used the same name and address of the inspections table in case that restaurant did not match with anybody. This way, in clean inspections all inspections have name and address, and all inspections whose restaurant matched have the same name and address (the authoritative one).


_Project done by Belen Michel Torino and Felipe Alamos_
