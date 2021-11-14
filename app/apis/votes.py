import pandas as pd
import numpy as np

from typing import List
from collections import defaultdict


def get_party_approvals(data_initiatives_votes: pd.DataFrame) -> pd.DataFrame:
    """
    Manipulate the votes to get the percentage of approval per party for each other party
    """
    
    # get all party votes fields
    parties_vote_direction_fields = [x for x in data_initiatives_votes.columns if x.startswith("iniciativa_votacao")]
    to_exclude = "iniciativa_votacao_res iniciativa_votacao_desc iniciativa_votacao_outros_afavor iniciativa_votacao_outros_abstenção iniciativa_votacao_outros_contra".split()
    parties_vote_direction_fields = list(set(parties_vote_direction_fields) - set(to_exclude))

    def calculate_vote_distribution(group: pd.DataFrame, parties_vote_direction_fields: List[str]) -> pd.Series:
        values = [
            group["iniciativa_aprovada"].count(),
            group["iniciativa_aprovada"].mean()
        ]

        # add the number of initiatives and % of approved initiatives for this group
        res = pd.Series(values, "total_iniciativas total_iniciativas_aprovadas".split())

        # add approval distribution per party
        res = res.append((group[parties_vote_direction_fields] == "afavor").mean())

        return res

    return data_initiatives_votes.groupby("iniciativa_autor").apply(lambda x: calculate_vote_distribution(x, parties_vote_direction_fields)).sort_values("total_iniciativas", ascending=False)


def get_party_correlations(data_initiatives_votes: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the number of times each party pair vote the same
    """

    # get all party votes fields
    parties_columns = [x for x in data_initiatives_votes.columns if x.startswith("iniciativa_votacao")]
    to_exclude = "iniciativa_votacao_res iniciativa_votacao_desc iniciativa_votacao_outros_afavor iniciativa_votacao_outros_abstenção iniciativa_votacao_outros_contra".split()
    parties_columns = list(set(parties_columns) - set(to_exclude))

    res = defaultdict(list)
    for party_a in parties_columns:
        for party_b in parties_columns:
            pa = data_initiatives_votes.loc[data_initiatives_votes[party_a] != "ausência", party_a]
            pb = data_initiatives_votes.loc[data_initiatives_votes[party_b] != "ausência", party_b]
            corr = pd.crosstab(pa, pb, margins=True)
            diag = np.diag(corr)
            
            total = diag[-1]
            total_corr = diag[:-1].sum()

            res[party_a].append(total_corr / total)

    return pd.DataFrame(res, index=parties_columns)


def get_initiatives(data_initiatives_votes: pd.DataFrame) -> pd.DataFrame:
    """
    Get all initiatives, removing not needed fields
    """
    
    # get needed fields' name
    parties_vote_direction_fields = [x for x in data_initiatives_votes.columns if x.startswith("iniciativa_votacao")]
    to_exclude = "iniciativa_votacao_res iniciativa_votacao_desc iniciativa_votacao_outros_afavor iniciativa_votacao_outros_abstenção iniciativa_votacao_outros_contra".split()
    parties_vote_direction_fields = list(set(parties_vote_direction_fields) - set(to_exclude))

    columns = ["iniciativa_evento_fase", "iniciativa_titulo", "iniciativa_url", "iniciativa_autor", "iniciativa_autor_deputado", "iniciativa_evento_data", "iniciativa_tipo", "iniciativa_votacao_res"] + parties_vote_direction_fields

    return data_initiatives_votes[columns].rename({
        "iniciativa_evento_data": "iniciativa_data",
        "iniciativa_evento_fase": "iniciativa_fase"
        }, axis="columns")
