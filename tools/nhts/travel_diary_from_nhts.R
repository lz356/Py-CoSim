# Creating travel diary from NHTS trip variable

#Install and load the NHTS data
install.packages("devtools")
devtools::install_github("Westat-Transportation/summarizeNHTS")

#Call all libraries
library(summarizeNHTS)
library(dplyr)
library(tidyr)

#Download the data into local directory for NHTS 2017
download_nhts_data("2017", exdir="C:/NHTS")
nhts_data <- read_data("2017", "C:/NHTS")

#Import files and assign them the variable names
Household<- nhts_data$data$household
Person<- nhts_data$data$person
Vehicle<- nhts_data$data$vehicle
Trip<- nhts_data$data$trip


# Subset the data for selecting nonEVs
EV_Vehicle<-subset(Vehicle, FUELTYPE != 3)
EV_Vehicle2 <- EV_Vehicle %>% sample_n(10)
filtered_EV<- Trip[, c("HOUSEID", "PERSONID", "VEHID", "STRTTIME", "ENDTIME", "TDTRPNUM")]

# Create a vector of unique HOUSEID values from the nonEV dataframe
unique_houseid <- as.numeric(unique(EV_Vehicle$HOUSEID))

# Subset the Trip dataframe based on matching HOUSEID values
subset_df2 <- filtered_EV[filtered_EV$HOUSEID %in% unique_houseid, ]

#convert trip time into time format
subset_df2$STRTTIME<-as.numeric(subset_df2$STRTTIME)
subset_df2$ENDTIME<-as.numeric(subset_df2$ENDTIME)
subset_df2$STRTTIME <- sprintf("%02d:%02d", subset_df2$STRTTIME %/% 100, subset_df2$STRTTIME %% 100)
subset_df2$ENDTIME <- sprintf("%02d:%02d", subset_df2$ENDTIME %/% 100, subset_df2$ENDTIME %% 100)

#Generate travel diary for each respondent in each household and the car they use
final_df <- subset_df2 %>%
  pivot_wider(names_from = TDTRPNUM,
              values_from = c(STRTTIME, ENDTIME, VEHID),
              names_prefix = "TRIP",
              values_fn = list(STRTTIME = first, ENDTIME = first, VEHID = first))

#merge household level and person level information to the dataset
merged_df <- merge(final_df, Household[, c("HOUSEID", "HHFAMINC", "HHVEHCNT","HHSIZE","HHSTATE","HH_RACE","HH_HISP","HH_CBSA","HBHUR" )], by ="HOUSEID")
merged_df2 <- merge(merged_df, Person[, c("HOUSEID","PERSONID","R_AGE_IMP", "R_SEX_IMP","DRIVER" )], by =c("HOUSEID","PERSONID"))

#Export data into csv format
write.csv(final_df, file = "NEV.csv", row.names = FALSE)
write.csv(merged_df2, file = "NEV2.csv", row.names = FALSE)