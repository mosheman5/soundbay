from pathlib import Path
import pandas as pd
import numpy as np
import re
import soundfile as sf
from typing import List


# <<<<<<< feature/EDA_script
def raven_to_df_annotations(annotations_path: str,
                            recording_path: str,
                            positive_tag_names: list = ['w', 'sc']):
    """
    Takes annotation files (selection table) created in Raven and turns it to a compatible annotations csv.
    """
    # create dataframe
    annotations = Path(annotations_path)
    recording = Path(recording_path)
    # ignore irrelevent files
    filelist = list(annotations.glob('*selections.txt'))
    metadata = []
    for file in filelist:
        dfTemp = pd.read_csv(file, sep="\t")
        dfTemp['filename'] = re.search(
            "\.Table.1.selections", file.as_posix()).group()
        dfTemp['FirstCallTime'] = np.amin(dfTemp['Begin Time (s)'])
        dfTemp['LastCallTime'] = np.amax(dfTemp['End Time (s)'])
        metadata.append(dfTemp)

    metadata = pd.concat(metadata)
    metadata.rename(columns={'Begin Time (s)': 'begin_time', 'End Time (s)': 'end_time'}, inplace=True)
    print('Number of Labels:', metadata.shape[0])
    for tag in positive_tag_names:
        metadata['Annotation'] = metadata['Annotation'].replace(np.nan, tag, regex=True)
    #add recording length to dataframe
    wav_filelist = list(recording.glob('*.wav'))
    wav_filedict = {re.search("\.Table.1.selections", file.as_posix()).group(): {'path': file} for file in wav_filelist
                    if re.search("\.Table.1.selections", file.as_posix())}
    for key, value in wav_filedict.items():
        record_length = sf.info(value['path']).duration
        value.update({'length': record_length})
        wav_filedict[key] = value
    annotation_lengths = []
    for filename_txt in metadata['filename']:
        annotation_lengths.append(wav_filedict[filename_txt]['length'])
    metadata['TotalRecordLength'] = annotation_lengths
    # filter metadata to contain only desired call types
    filters = positive_tag_names
    metadata_calls = metadata[metadata.Annotation.isin(filters)]
    unique_files, idx = np.unique(metadata['filename'], return_index=True)
    # Find true length of call sequences ( get rid of over lapping-sequences)
    non_overlap_all = non_overlap_df(metadata)
    non_overlap_calls = non_overlap_df(metadata_calls)
    # label background segments
    bg_segments = []
    for file in (unique_files):
        file_df = non_overlap_all[non_overlap_all['filename'] == file]
        begin = np.array(file_df['begin_time'])
        end = np.array(file_df['end_time'])
        for item in end:
            next_beginning = begin[begin > item]
            if next_beginning.size == 0:
                break
            next_beginning = np.min(next_beginning)
            bg_segments.append([item, next_beginning, file])
    bg_segments = pd.DataFrame(bg_segments, columns=['begin_time', 'end_time', 'filename'])
    bg_segments['call_length'] = bg_segments['end_time'] - bg_segments['begin_time']
    bg_segments.sort_values(by=['call_length'])
    # add labels: 0 for background and 1 for call. TODO: modify if there are different call types
    bg_segments['label'] = np.zeros(bg_segments.shape[0], dtype=int)
    non_overlap_calls['label'] = np.ones(non_overlap_calls.shape[0], dtype=int)
    # combine to a csv
    combined_annotations = pd.concat([bg_segments, non_overlap_calls])
    return combined_annotations


def annotations_df_to_csv(annotations_dataset, dataset_name: str = 'recordings_2018_filtered'):
    filename = 'combined_annotations_' + dataset_name + '.csv'
    annotations_dataset.to_csv(filename, index=False)


def merge_calls(sorted_df: pd.DataFrame) -> List[pd.Series]:
    """
    Args:
        sorted_df: DataFrame with sorted calls by begin_time field

    Returns: List of non-overlapping merged calls from the DataFrame
    """
    merged = [sorted_df.iloc[0].copy()]
    for _, higher in sorted_df.iterrows():
        lower = merged[-1]
        # test for intersection between lower and higher:
        # we know via sorting that lower[0] <= higher[0]
        if higher.begin_time <= lower.end_time:
            max_end_time = max(lower.end_time, higher.end_time)
            merged[-1].end_time = max_end_time  # replace by merged interval
        else:
            merged.append(higher.copy())
    return merged


def non_overlap_df(input_df: pd.DataFrame) -> pd.DataFrame:
    """
    Args:
        input_df: DataFrame with possibly overlapping calls

    Returns: a DataFrame object with non-overlapping calls (after merge).
    """
    non_overlap = []
    for file_name, file_df in input_df.groupby(by='filename'):
        file_df.sort_values(by='begin_time', inplace=True)
        merged = merge_calls(file_df)
        non_overlap.extend(merged)
    non_overlap = pd.DataFrame(non_overlap)
    non_overlap['call_length'] = non_overlap['end_time'] - non_overlap['begin_time']

    return non_overlap


def get_metadata_fields():
    return ['begin_time', 'end_time', 'filename', 'call_length', 'label']
